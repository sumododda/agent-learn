"""Post-processing fixes for LLM-generated markdown content."""
import re

# ---------------------------------------------------------------------------
# Table normalizer — fix single-line collapsed tables
# ---------------------------------------------------------------------------

# Matches a pipe-delimited row boundary: "| |" where the second pipe starts
# a new row (header, separator, or data row).  We detect this by looking for
# `| |` patterns where the second `|` is followed by content typical of a
# new table row.
_COLLAPSED_TABLE_RE = re.compile(
    r"\|[ \t]*\|[ \t]*(?=(?:[:\-]|[A-Za-z0-9*_]))"
)


def normalize_tables(markdown: str) -> str:
    """Split single-line markdown tables into proper multi-line format."""
    lines = markdown.split("\n")
    result: list[str] = []

    for line in lines:
        stripped = line.strip()

        # Only process lines that look like collapsed tables:
        # - start and end with |
        # - contain a separator pattern like | :--- |
        # - have more than 3 pipe characters (minimum for a 2-col single row)
        if (
            stripped.startswith("|")
            and stripped.endswith("|")
            and stripped.count("|") > 6  # at least 2 rows of a 2-col table
            and re.search(r"\|[\s:]*-{3,}", stripped)  # contains separator row
        ):
            result.append(_split_table_rows(stripped))
        else:
            result.append(line)

    return "\n".join(result)


def _split_table_rows(collapsed: str) -> str:
    """Split a collapsed single-line table into separate rows.

    The trick: a separator row (| :--- | :--- |) marks the boundary between
    header and data.  We find it, then split around each `| |` boundary.
    """
    # Find the separator pattern and split there first
    # Pattern: | :---: | :--- | or | --- | --- |
    sep_match = re.search(r"(\|[\s:]*-{3,}[\s:]*(?:\|[\s:]*-{3,}[\s:]*)+\|)", collapsed)
    if not sep_match:
        return collapsed

    sep = sep_match.group(1).strip()
    sep_start = sep_match.start()
    sep_end = sep_match.end()

    # Header is everything before the separator
    header = collapsed[:sep_start].strip()
    # Data rows are everything after
    data = collapsed[sep_end:].strip()

    # Split data rows: each row starts with | and ends with |
    # Pattern: find all |...|...|...| sequences
    data_rows = _extract_rows(data)

    rows = [header, sep] + data_rows
    return "\n".join(row for row in rows if row.strip())


def _extract_rows(data: str) -> list[str]:
    """Extract individual table rows from collapsed data."""
    rows: list[str] = []
    if not data.strip():
        return rows

    # Split on `| |` boundaries, keeping the pipes
    # Each row looks like: | cell | cell | cell |
    parts = re.split(r"\|(?=\s*\|)", data)

    current = ""
    for part in parts:
        if current:
            candidate = current + "|"
            # If adding this part would start a new row (current ends with |
            # and looks complete), emit current and start fresh
            if candidate.strip().endswith("|") and candidate.strip().startswith("|"):
                cell_count = candidate.count("|") - 1
                if cell_count >= 2:
                    rows.append(candidate.strip())
                    current = part
                    continue
        current = (current + "|" + part) if current else part

    if current and current.strip():
        final = current.strip()
        if not final.endswith("|"):
            final += " |"
        rows.append(final)

    return rows


# ---------------------------------------------------------------------------
# Combined post-processing
# ---------------------------------------------------------------------------

async def postprocess_content(
    markdown: str,
    provider: str,
    model: str,
    credentials: dict,
) -> str:
    """Run all content post-processing fixes."""
    from app.mermaid_lint import repair_mermaid_in_content

    # Fix collapsed tables
    result = normalize_tables(markdown)

    # Validate and repair mermaid diagrams
    result = await repair_mermaid_in_content(result, provider, model, credentials)

    return result
