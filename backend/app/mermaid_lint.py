"""Regex-based mermaid syntax validator and LLM repair for generated content."""
import logging
import re

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

_MERMAID_BLOCK_RE = re.compile(
    r"```mermaid\s*\n(.*?)```", re.DOTALL
)


def extract_mermaid_blocks(markdown: str) -> list[tuple[str, int, int]]:
    """Return list of (mermaid_code, start, end) from markdown fenced blocks."""
    return [
        (m.group(1).strip(), m.start(), m.end())
        for m in _MERMAID_BLOCK_RE.finditer(markdown)
    ]


# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

_VALID_DIAGRAM_TYPES = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|erDiagram|gantt|pie|gitgraph|journey|mindmap|timeline|quadrantChart|xychart|block-beta)",
    re.MULTILINE,
)

_STYLE_DIRECTIVE_RE = re.compile(
    r"^\s*(style|classDef|linkStyle|click)\s", re.MULTILINE
)

_UNBALANCED_BRACKET_CHARS = {"[", "]", "{", "}", "(", ")"}


def _check_bracket_balance(code: str) -> str | None:
    """Check for unbalanced brackets in non-label contexts."""
    # Count brackets outside of quoted strings
    stack: list[str] = []
    pairs = {"]": "[", "}": "{", ")": "("}
    in_quotes = False
    quote_char = ""

    for ch in code:
        if ch in ('"', "'") and not in_quotes:
            in_quotes = True
            quote_char = ch
        elif ch == quote_char and in_quotes:
            in_quotes = False
        elif not in_quotes:
            if ch in ("[", "{", "("):
                stack.append(ch)
            elif ch in pairs:
                if not stack or stack[-1] != pairs[ch]:
                    return f"Unbalanced closing '{ch}'"
                stack.pop()

    if stack:
        return f"Unbalanced opening '{stack[-1]}'"
    return None


_BAD_ARROW_RE = re.compile(r"--+>-|<=+>|<-+<|>-+>")

_EMPTY_NODE_LABEL_RE = re.compile(r'\w+\[\s*\]')


def validate_mermaid(code: str) -> list[str]:
    """Validate mermaid code and return list of error descriptions. Empty = valid."""
    errors: list[str] = []

    stripped = code.strip()
    if not stripped:
        errors.append("Empty diagram")
        return errors

    # Must start with a recognized diagram type
    if not _VALID_DIAGRAM_TYPES.match(stripped):
        first_line = stripped.split("\n")[0].strip()
        errors.append(f"Unknown diagram type: '{first_line}'")

    # No style directives (they break strict security mode)
    for m in _STYLE_DIRECTIVE_RE.finditer(stripped):
        errors.append(f"Style directive not allowed: '{m.group(0).strip()}'")

    # Bracket balance
    bracket_err = _check_bracket_balance(stripped)
    if bracket_err:
        errors.append(bracket_err)

    # Malformed arrows
    for m in _BAD_ARROW_RE.finditer(stripped):
        errors.append(f"Malformed arrow: '{m.group(0)}'")

    # Empty node labels
    for m in _EMPTY_NODE_LABEL_RE.finditer(stripped):
        errors.append(f"Empty node label: '{m.group(0)}'")

    return errors


# ---------------------------------------------------------------------------
# Content-level validation and LLM repair
# ---------------------------------------------------------------------------


def validate_mermaid_in_content(markdown: str) -> list[tuple[str, list[str]]]:
    """Validate all mermaid blocks in markdown content.
    Returns list of (mermaid_code, errors) for blocks that have errors.
    """
    results = []
    for code, _, _ in extract_mermaid_blocks(markdown):
        errors = validate_mermaid(code)
        if errors:
            results.append((code, errors))
    return results


async def repair_mermaid_in_content(
    markdown: str,
    provider: str,
    model: str,
    credentials: dict,
) -> str:
    """Validate mermaid blocks and ask LLM to repair any broken ones.
    Returns the markdown with repaired mermaid blocks (or original if all valid).
    """
    blocks = extract_mermaid_blocks(markdown)
    if not blocks:
        return markdown

    repairs: list[tuple[str, str]] = []  # (original, fixed)

    for code, _, _ in blocks:
        errors = validate_mermaid(code)
        if not errors:
            continue

        logger.warning("[mermaid_lint] Found %d errors in diagram: %s", len(errors), errors)

        fixed = await _ask_llm_to_fix(code, errors, provider, model, credentials)
        if fixed and not validate_mermaid(fixed):
            repairs.append((code, fixed))
            logger.info("[mermaid_lint] Successfully repaired diagram")
        else:
            # LLM fix still broken — remove the mermaid block entirely
            repairs.append((code, None))
            logger.warning("[mermaid_lint] LLM repair still invalid, removing diagram")

    if not repairs:
        return markdown

    result = markdown
    for original, fixed in repairs:
        if fixed:
            result = result.replace(
                f"```mermaid\n{original}\n```",
                f"```mermaid\n{fixed}\n```",
            )
        else:
            # Remove broken diagram entirely
            result = result.replace(f"```mermaid\n{original}\n```", "")

    return result


async def _ask_llm_to_fix(
    code: str,
    errors: list[str],
    provider: str,
    model: str,
    credentials: dict,
) -> str | None:
    """Ask the LLM to fix a broken mermaid diagram."""
    from app.provider_service import build_chat_model

    llm = build_chat_model(provider, model, credentials)
    error_list = "\n".join(f"- {e}" for e in errors)

    prompt = (
        "Fix this mermaid diagram. Return ONLY the corrected mermaid code, no markdown fences, no explanation.\n\n"
        "Rules:\n"
        "- Use `flowchart TD` or `sequenceDiagram` only\n"
        "- Always quote labels with brackets: A[\"Label text\"]\n"
        '- No parentheses or special chars in unquoted labels\n'
        "- No `style`, `classDef`, `linkStyle`, or `click` directives\n"
        "- Use simple arrows: `-->`, `==>`, `-.->` only\n"
        "- Keep under 15 nodes\n\n"
        f"Errors found:\n{error_list}\n\n"
        f"Broken diagram:\n{code}"
    )

    try:
        result = await llm.ainvoke(prompt)
        fixed = result.content.strip()
        # Strip any markdown fences the LLM might add
        fixed = re.sub(r"^```(?:mermaid)?\s*\n?", "", fixed)
        fixed = re.sub(r"\n?```\s*$", "", fixed)
        return fixed.strip()
    except Exception as e:
        logger.warning("[mermaid_lint] LLM repair call failed: %s", e)
        return None
