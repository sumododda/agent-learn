#!/usr/bin/env bash
set -euo pipefail

# ─── Configuration ───────────────────────────────────────────────────────────
NAMESPACE="agent-learn"
CERT_NAME="agent-learn-cert"
INGRESS_NAME="agent-learn-ingress"
DOMAIN="learn.blekcipher.com"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

pass() { echo -e "  ${GREEN}✔${NC} $*"; }
fail() { echo -e "  ${RED}✘${NC} $*"; }
warn() { echo -e "  ${YELLOW}!${NC} $*"; }
info() { echo -e "  ${CYAN}→${NC} $*"; }

ERRORS=0

# ─── 1. GKE Managed Certificate Status ──────────────────────────────────────
echo -e "\n${CYAN}[1/4] GKE Managed Certificate${NC}"

if kubectl get managedcertificate "$CERT_NAME" -n "$NAMESPACE" &>/dev/null; then
  CERT_STATUS=$(kubectl get managedcertificate "$CERT_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.status.certificateStatus}' 2>/dev/null || echo "Unknown")

  case "$CERT_STATUS" in
    Active)
      pass "Managed certificate status: ${GREEN}Active${NC}"
      ;;
    Provisioning)
      warn "Managed certificate status: ${YELLOW}Provisioning${NC} (can take 15-60 min)"
      ;;
    *)
      fail "Managed certificate status: ${RED}${CERT_STATUS}${NC}"
      ERRORS=$((ERRORS + 1))
      ;;
  esac

  CERT_DOMAINS=$(kubectl get managedcertificate "$CERT_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.spec.domains[*]}' 2>/dev/null || echo "none")
  info "Domains: $CERT_DOMAINS"

  CERT_EXPIRE=$(kubectl get managedcertificate "$CERT_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.status.expireTime}' 2>/dev/null || echo "")
  if [[ -n "$CERT_EXPIRE" ]]; then
    info "Expires: $CERT_EXPIRE"
  fi
else
  fail "Managed certificate '$CERT_NAME' not found in namespace '$NAMESPACE'"
  ERRORS=$((ERRORS + 1))
fi

# ─── 2. Ingress Status ──────────────────────────────────────────────────────
echo -e "\n${CYAN}[2/4] Ingress Configuration${NC}"

if kubectl get ingress "$INGRESS_NAME" -n "$NAMESPACE" &>/dev/null; then
  INGRESS_IP=$(kubectl get ingress "$INGRESS_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "")

  if [[ -n "$INGRESS_IP" ]]; then
    pass "Ingress external IP: $INGRESS_IP"
  else
    fail "Ingress has no external IP assigned yet"
    ERRORS=$((ERRORS + 1))
  fi

  MANAGED_CERT_ANNOTATION=$(kubectl get ingress "$INGRESS_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.annotations.networking\.gke\.io/managed-certificates}' 2>/dev/null || echo "")

  if [[ "$MANAGED_CERT_ANNOTATION" == "$CERT_NAME" ]]; then
    pass "Ingress references managed certificate: $CERT_NAME"
  else
    fail "Ingress managed-certificates annotation: '${MANAGED_CERT_ANNOTATION:-missing}' (expected '$CERT_NAME')"
    ERRORS=$((ERRORS + 1))
  fi

  ALLOW_HTTP=$(kubectl get ingress "$INGRESS_NAME" -n "$NAMESPACE" \
    -o jsonpath='{.metadata.annotations.kubernetes\.io/ingress\.allow-http}' 2>/dev/null || echo "")

  if [[ "$ALLOW_HTTP" == "false" ]]; then
    pass "HTTP blocked (HTTPS only)"
  else
    warn "HTTP is allowed — consider setting ingress.allow-http: \"false\""
  fi
else
  fail "Ingress '$INGRESS_NAME' not found in namespace '$NAMESPACE'"
  ERRORS=$((ERRORS + 1))
fi

# ─── 3. DNS Resolution ──────────────────────────────────────────────────────
echo -e "\n${CYAN}[3/4] DNS Resolution${NC}"

if command -v dig &>/dev/null; then
  DNS_RESULT=$(dig +short "$DOMAIN" 2>/dev/null | head -5)
  if [[ -n "$DNS_RESULT" ]]; then
    pass "DNS resolves for $DOMAIN:"
    echo "$DNS_RESULT" | while read -r line; do
      info "$line"
    done
  else
    fail "DNS does not resolve for $DOMAIN"
    ERRORS=$((ERRORS + 1))
  fi
elif command -v nslookup &>/dev/null; then
  DNS_RESULT=$(nslookup "$DOMAIN" 2>/dev/null | grep -A2 "Name:" | tail -1 || echo "")
  if [[ -n "$DNS_RESULT" ]]; then
    pass "DNS resolves for $DOMAIN"
    info "$DNS_RESULT"
  else
    fail "DNS does not resolve for $DOMAIN"
    ERRORS=$((ERRORS + 1))
  fi
else
  warn "Neither dig nor nslookup available — skipping DNS check"
fi

# ─── 4. Live TLS Certificate ────────────────────────────────────────────────
echo -e "\n${CYAN}[4/4] Live TLS Certificate${NC}"

if command -v openssl &>/dev/null; then
  CERT_OUTPUT=$(echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN:443" 2>/dev/null)

  if [[ -n "$CERT_OUTPUT" ]]; then
    SUBJECT=$(echo "$CERT_OUTPUT" | openssl x509 -noout -subject 2>/dev/null || echo "")
    ISSUER=$(echo "$CERT_OUTPUT" | openssl x509 -noout -issuer 2>/dev/null || echo "")
    DATES=$(echo "$CERT_OUTPUT" | openssl x509 -noout -dates 2>/dev/null || echo "")
    SANS=$(echo "$CERT_OUTPUT" | openssl x509 -noout -ext subjectAltName 2>/dev/null || echo "")

    if [[ -n "$SUBJECT" ]]; then
      pass "TLS certificate found"
      info "$SUBJECT"
      info "$ISSUER"
      echo "$DATES" | while read -r line; do
        [[ -n "$line" ]] && info "$line"
      done

      # Check expiry
      NOT_AFTER=$(echo "$CERT_OUTPUT" | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
      if [[ -n "$NOT_AFTER" ]]; then
        EXPIRY_EPOCH=$(date -d "$NOT_AFTER" +%s 2>/dev/null || echo "")
        NOW_EPOCH=$(date +%s)
        if [[ -n "$EXPIRY_EPOCH" ]]; then
          DAYS_LEFT=$(( (EXPIRY_EPOCH - NOW_EPOCH) / 86400 ))
          if [[ $DAYS_LEFT -lt 0 ]]; then
            fail "Certificate EXPIRED $((DAYS_LEFT * -1)) days ago"
            ERRORS=$((ERRORS + 1))
          elif [[ $DAYS_LEFT -lt 14 ]]; then
            warn "Certificate expires in ${DAYS_LEFT} days"
          else
            pass "Certificate valid for ${DAYS_LEFT} days"
          fi
        fi
      fi

      # Verify chain
      VERIFY=$(echo "$CERT_OUTPUT" | grep "Verify return code" || echo "")
      if echo "$VERIFY" | grep -q "0 (ok)"; then
        pass "Certificate chain verified"
      else
        fail "Certificate chain issue: $VERIFY"
        ERRORS=$((ERRORS + 1))
      fi
    else
      fail "Could not parse TLS certificate from $DOMAIN:443"
      ERRORS=$((ERRORS + 1))
    fi
  else
    fail "Could not connect to $DOMAIN:443"
    ERRORS=$((ERRORS + 1))
  fi
else
  warn "openssl not available — skipping live TLS check"
fi

# ─── Summary ─────────────────────────────────────────────────────────────────
echo
if [[ $ERRORS -eq 0 ]]; then
  echo -e "${GREEN}All checks passed.${NC}"
else
  echo -e "${RED}${ERRORS} check(s) failed.${NC}"
  exit 1
fi
