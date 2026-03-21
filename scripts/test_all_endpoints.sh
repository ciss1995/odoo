#!/usr/bin/env bash
# Comprehensive test suite for ALL base_api endpoints.
# Uses admin API key by default, tests session auth flow too.
set -euo pipefail

BASE="http://localhost:8069"
API_KEY="bc48875ec3b34bced7eacc7c8e35820df02fb28a"
ADMIN_KEY="$API_KEY"
REGULAR_KEY="03e201ed63f4fb16a0e301a53a7a96d7f3b2ca11"
PASS=0
FAIL=0
TOTAL=0

ok()   { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); echo "  ✓ PASS: $1"; }
fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); echo "  ✗ FAIL: $1 -- $2"; }

check() {
    local name="$1" url="$2" method="${3:-GET}" key="${4:-$API_KEY}" body="${5:-}"
    local args=(-s -w "\n%{http_code}" -X "$method")
    [[ -n "$key" ]] && args+=(-H "api-key: $key")
    [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")
    
    local resp
    resp=$(curl "${args[@]}" "${BASE}${url}")
    local code
    code=$(echo "$resp" | tail -1)
    local json
    json=$(echo "$resp" | sed '$d')
    
    local success
    success=$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null || echo "parse_error")
    
    if [[ "$code" =~ ^2 ]] && [[ "$success" == "True" ]]; then
        ok "$name (HTTP $code)"
    else
        fail "$name" "HTTP $code, success=$success"
        echo "      Response: $(echo "$json" | head -c 200)"
    fi
}

check_session() {
    local name="$1" url="$2" method="${3:-GET}" token="$4" body="${5:-}"
    local args=(-s -w "\n%{http_code}" -X "$method" -H "session-token: $token")
    [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")
    
    local resp
    resp=$(curl "${args[@]}" "${BASE}${url}")
    local code
    code=$(echo "$resp" | tail -1)
    local json
    json=$(echo "$resp" | sed '$d')
    
    local success
    success=$(echo "$json" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('success', False))" 2>/dev/null || echo "parse_error")
    
    if [[ "$code" =~ ^2 ]] && [[ "$success" == "True" ]]; then
        ok "$name (HTTP $code)"
    else
        fail "$name" "HTTP $code, success=$success"
        echo "      Response: $(echo "$json" | head -c 200)"
    fi
}

echo "=============================================="
echo " base_api Comprehensive Test Suite"
echo "=============================================="

# ──────────────────────────────────────────────────
echo ""
echo "--- 1. Health / No-Auth Endpoints ---"
check "GET /api/v2/test" "/api/v2/test"

# ──────────────────────────────────────────────────
echo ""
echo "--- 2. API-Key Authentication ---"
check "GET /api/v2/auth/test (admin key)" "/api/v2/auth/test"
check "GET /api/v2/auth/test (regular key)" "/api/v2/auth/test" "GET" "$REGULAR_KEY"

# ──────────────────────────────────────────────────
echo ""
echo "--- 3. User Info ---"
check "GET /api/v2/user/info" "/api/v2/user/info"
check "GET /api/v2/auth/me" "/api/v2/auth/me"

# ──────────────────────────────────────────────────
echo ""
echo "--- 4. Session Auth Flow (login → me → refresh → logout) ---"
LOGIN_RESP=$(curl -s -X POST "${BASE}/api/v2/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin"}')
SESSION_TOKEN=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['session_token'])" 2>/dev/null || echo "")
if [[ -n "$SESSION_TOKEN" && "$SESSION_TOKEN" != "" ]]; then
    ok "POST /api/v2/auth/login"
else
    fail "POST /api/v2/auth/login" "no session token"
    SESSION_TOKEN=""
fi

if [[ -n "$SESSION_TOKEN" ]]; then
    check_session "GET /api/v2/auth/me (session)" "/api/v2/auth/me" "GET" "$SESSION_TOKEN"

    REFRESH_RESP=$(curl -s -X POST "${BASE}/api/v2/auth/refresh" -H "session-token: $SESSION_TOKEN")
    NEW_TOKEN=$(echo "$REFRESH_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['data']['session_token'])" 2>/dev/null || echo "")
    if [[ -n "$NEW_TOKEN" && "$NEW_TOKEN" != "" ]]; then
        ok "POST /api/v2/auth/refresh"
        SESSION_TOKEN="$NEW_TOKEN"
    else
        fail "POST /api/v2/auth/refresh" "no new token"
    fi

    check_session "POST /api/v2/auth/logout" "/api/v2/auth/logout" "POST" "$SESSION_TOKEN"
else
    fail "GET /api/v2/auth/me (session)" "skipped - no token"
    fail "POST /api/v2/auth/refresh" "skipped - no token"
    fail "POST /api/v2/auth/logout" "skipped - no token"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 5. Partners ---"
check "GET /api/v2/partners" "/api/v2/partners"
check "GET /api/v2/partners (customers_only=false)" "/api/v2/partners?customers_only=false&limit=5"

# ──────────────────────────────────────────────────
echo ""
echo "--- 6. Products ---"
check "GET /api/v2/products" "/api/v2/products"
check "GET /api/v2/products (limit=3)" "/api/v2/products?limit=3"

# ──────────────────────────────────────────────────
echo ""
echo "--- 7. Generic Search (sale, hr, crm) ---"
check "GET search res.partner" "/api/v2/search/res.partner?limit=5"
check "GET search product.template" "/api/v2/search/product.template?limit=5"
check "GET search sale.order" "/api/v2/search/sale.order?limit=5&fields=name,partner_id,amount_total,state"
check "GET search hr.employee" "/api/v2/search/hr.employee?limit=5&fields=name,job_title,department_id,work_email"
check "GET search hr.department" "/api/v2/search/hr.department?limit=5"
check "GET search crm.lead" "/api/v2/search/crm.lead?limit=5&fields=name,partner_id,expected_revenue,type,priority"

# ──────────────────────────────────────────────────
echo ""
echo "--- 8. Get Record by ID ---"
check "GET search/res.partner/1" "/api/v2/search/res.partner/1?fields=id,name,email,phone"

# ──────────────────────────────────────────────────
echo ""
echo "--- 9. Model Fields ---"
check "GET fields res.partner" "/api/v2/fields/res.partner"
check "GET fields sale.order" "/api/v2/fields/sale.order"
check "GET fields hr.employee" "/api/v2/fields/hr.employee"
check "GET fields crm.lead" "/api/v2/fields/crm.lead"

# ──────────────────────────────────────────────────
echo ""
echo "--- 10. Model Discovery ---"
check "GET /api/v2/models" "/api/v2/models?search=sale"
check "GET /api/v2/models (hr)" "/api/v2/models?search=hr"
check "GET /api/v2/models (crm)" "/api/v2/models?search=crm"

# ──────────────────────────────────────────────────
echo ""
echo "--- 11. Groups ---"
check "GET /api/v2/groups" "/api/v2/groups"

# ──────────────────────────────────────────────────
echo ""
echo "--- 12. Users ---"
check "GET /api/v2/users" "/api/v2/users"
check "GET /api/v2/users (search)" "/api/v2/users?search=admin"
check "GET /api/v2/users/2" "/api/v2/users/2"

# ──────────────────────────────────────────────────
echo ""
echo "--- 13. Create Record (generic) ---"
CREATE_RESP=$(curl -s -X POST "${BASE}/api/v2/create/res.partner" \
    -H "api-key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"name":"Test API Partner","email":"testpartner@api.com","phone":"+1-555-0199","is_company":false,"customer_rank":1}')
CREATE_SUCCESS=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success', False))" 2>/dev/null || echo "parse_error")
CREATED_PARTNER_ID=$(echo "$CREATE_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('id',''))" 2>/dev/null || echo "")
if [[ "$CREATE_SUCCESS" == "True" ]]; then
    ok "POST /api/v2/create/res.partner (id=$CREATED_PARTNER_ID)"
else
    fail "POST /api/v2/create/res.partner" "success=$CREATE_SUCCESS"
    echo "      Response: $(echo "$CREATE_RESP" | head -c 200)"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 14. Update Record (generic) ---"
if [[ -n "$CREATED_PARTNER_ID" && "$CREATED_PARTNER_ID" != "" ]]; then
    check "PUT /api/v2/update/res.partner/$CREATED_PARTNER_ID" "/api/v2/update/res.partner/$CREATED_PARTNER_ID" "PUT" "$API_KEY" '{"phone":"+1-555-UPDATED"}'
else
    fail "PUT /api/v2/update/res.partner" "no partner ID from create step"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 15. Delete Record (generic) ---"
if [[ -n "$CREATED_PARTNER_ID" && "$CREATED_PARTNER_ID" != "" ]]; then
    check "DELETE /api/v2/delete/res.partner/$CREATED_PARTNER_ID" "/api/v2/delete/res.partner/$CREATED_PARTNER_ID" "DELETE" "$API_KEY"
else
    fail "DELETE /api/v2/delete/res.partner" "no partner ID from create step"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 16. Create User (with auto credentials) ---"
CREATE_USER_RESP=$(curl -s -X POST "${BASE}/api/v2/create/res.users" \
    -H "api-key: $API_KEY" \
    -H "Content-Type: application/json" \
    -d '{"name":"API Test User","login":"apitest@test.com","email":"apitest@test.com"}')
CREATE_USER_OK=$(echo "$CREATE_USER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('success', False))" 2>/dev/null || echo "parse_error")
CREATED_USER_ID=$(echo "$CREATE_USER_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin).get('data',{}).get('id',''))" 2>/dev/null || echo "")
if [[ "$CREATE_USER_OK" == "True" ]]; then
    ok "POST /api/v2/create/res.users (id=$CREATED_USER_ID)"
else
    fail "POST /api/v2/create/res.users" "success=$CREATE_USER_OK"
    echo "      Response: $(echo "$CREATE_USER_RESP" | head -c 300)"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 17. Update User ---"
if [[ -n "$CREATED_USER_ID" && "$CREATED_USER_ID" != "" ]]; then
    check "PUT /api/v2/users/$CREATED_USER_ID (update name)" "/api/v2/users/$CREATED_USER_ID" "PUT" "$API_KEY" '{"name":"API Test User Updated","phone":"+1-555-0202"}'
else
    fail "PUT /api/v2/users/<id>" "no user ID from create step"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 18. Change Password ---"
if [[ -n "$CREATED_USER_ID" && "$CREATED_USER_ID" != "" ]]; then
    check "PUT /api/v2/users/$CREATED_USER_ID/password (admin reset)" "/api/v2/users/$CREATED_USER_ID/password" "PUT" "$API_KEY" '{"new_password":"newpass123"}'
else
    fail "PUT /api/v2/users/<id>/password" "no user ID"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 19. Reset Password (admin) ---"
if [[ -n "$CREATED_USER_ID" && "$CREATED_USER_ID" != "" ]]; then
    check "POST /api/v2/users/$CREATED_USER_ID/reset-password" "/api/v2/users/$CREATED_USER_ID/reset-password" "POST" "$API_KEY"
else
    fail "POST /api/v2/users/<id>/reset-password" "no user ID"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 20. Generate API Key ---"
if [[ -n "$CREATED_USER_ID" && "$CREATED_USER_ID" != "" ]]; then
    check "POST /api/v2/users/$CREATED_USER_ID/api-key" "/api/v2/users/$CREATED_USER_ID/api-key" "POST" "$API_KEY"
else
    fail "POST /api/v2/users/<id>/api-key" "no user ID"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 21. Access Control (regular user restrictions) ---"
GROUPS_RESP=$(curl -s "${BASE}/api/v2/groups" -H "api-key: $REGULAR_KEY")
GROUPS_OK=$(echo "$GROUPS_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ACCESS_DENIED' in str(d))" 2>/dev/null || echo "parse_error")
if [[ "$GROUPS_OK" == "True" ]]; then
    ok "GET /api/v2/groups (regular user → access denied)"
else
    fail "GET /api/v2/groups (regular user)" "expected access denied"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 22. Error Handling ---"
NOKEY_RESP=$(curl -s "${BASE}/api/v2/auth/test")
NOKEY_OK=$(echo "$NOKEY_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
if [[ "$NOKEY_OK" == "MISSING_API_KEY" ]]; then
    ok "No API key → MISSING_API_KEY error"
else
    fail "No API key error" "expected MISSING_API_KEY, got $NOKEY_OK"
fi

BAD_MODEL=$(curl -s "${BASE}/api/v2/search/fake.model" -H "api-key: $API_KEY")
BAD_OK=$(echo "$BAD_MODEL" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
if [[ "$BAD_OK" == "MODEL_NOT_FOUND" ]]; then
    ok "Invalid model → MODEL_NOT_FOUND error"
else
    fail "Invalid model error" "expected MODEL_NOT_FOUND, got $BAD_OK"
fi

NOT_FOUND=$(curl -s "${BASE}/api/v2/search/res.partner/999999" -H "api-key: $API_KEY")
NF_OK=$(echo "$NOT_FOUND" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('error',{}).get('code',''))" 2>/dev/null || echo "")
if [[ "$NF_OK" == "RECORD_NOT_FOUND" ]]; then
    ok "Non-existent record → RECORD_NOT_FOUND"
else
    fail "Non-existent record error" "expected RECORD_NOT_FOUND, got $NF_OK"
fi

# ──────────────────────────────────────────────────
echo ""
echo "--- 23. Cross-Module Searches ---"
check "search sale.order.line" "/api/v2/search/sale.order.line?limit=5&fields=order_id,product_id,product_uom_qty,price_unit"
check "search product.product" "/api/v2/search/product.product?limit=5&fields=name,default_code,list_price"
check "search product.category" "/api/v2/search/product.category?limit=5"
check "search crm.team" "/api/v2/search/crm.team?limit=5"
check "search res.company" "/api/v2/search/res.company?limit=5"
check "search res.users" "/api/v2/search/res.users?limit=5&fields=name,login,email"
check "search ir.module.module" "/api/v2/search/ir.module.module?limit=5&fields=name,state,shortdesc&state=installed"

# ──────────────────────────────────────────────────
# Cleanup: delete the test user
if [[ -n "$CREATED_USER_ID" && "$CREATED_USER_ID" != "" ]]; then
    curl -s -X DELETE "${BASE}/api/v2/delete/res.users/$CREATED_USER_ID" -H "api-key: $API_KEY" > /dev/null 2>&1 || true
fi

echo ""
echo "=============================================="
echo " Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "=============================================="
[[ $FAIL -eq 0 ]] && echo " ALL TESTS PASSED" || echo " SOME TESTS FAILED"
exit $FAIL
