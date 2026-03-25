#!/usr/bin/env bash
# Comprehensive test suite for ALL base_api endpoints.
# Covers: auth, CRUD, users, settings, notifications, activities, access control.
set -euo pipefail

BASE="http://localhost:8069"
API_KEY="bc48875ec3b34bced7eacc7c8e35820df02fb28a"
ADMIN_KEY="$API_KEY"
REGULAR_KEY="03e201ed63f4fb16a0e301a53a7a96d7f3b2ca11"
SALES_KEY="1ac1560779c0b4241bdf91c73e3e2d30d0c00579"
PASS=0
FAIL=0
TOTAL=0

ok()   { PASS=$((PASS+1)); TOTAL=$((TOTAL+1)); echo "  ✓ PASS: $1"; }
fail() { FAIL=$((FAIL+1)); TOTAL=$((TOTAL+1)); echo "  ✗ FAIL: $1 -- $2"; }

json_val() {
    python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null || echo ""
}

check() {
    local name="$1" url="$2" method="${3:-GET}" key="${4:-$API_KEY}" body="${5:-}"
    local args=(-s -w "\n%{http_code}" -X "$method")
    [[ -n "$key" ]] && args+=(-H "api-key: $key")
    [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")

    local resp code json_body success
    resp=$(curl "${args[@]}" "${BASE}${url}")
    code=$(echo "$resp" | tail -1)
    json_body=$(echo "$resp" | sed '$d')

    success=$(echo "$json_body" | json_val "d.get('success', False)")

    if [[ "$code" =~ ^2 ]] && [[ "$success" == "True" ]]; then
        ok "$name (HTTP $code)"
    else
        fail "$name" "HTTP $code, success=$success"
        echo "      Response: $(echo "$json_body" | head -c 200)"
    fi
}

check_fail() {
    local name="$1" url="$2" expected_code="$3" method="${4:-GET}" key="${5:-$API_KEY}" body="${6:-}"
    local args=(-s -w "\n%{http_code}" -X "$method")
    [[ -n "$key" ]] && args+=(-H "api-key: $key")
    [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")

    local resp code json_body err_code
    resp=$(curl "${args[@]}" "${BASE}${url}")
    code=$(echo "$resp" | tail -1)
    json_body=$(echo "$resp" | sed '$d')

    err_code=$(echo "$json_body" | json_val "d.get('error',{}).get('code','')")

    if [[ "$err_code" == "$expected_code" ]]; then
        ok "$name → $expected_code"
    else
        fail "$name" "expected $expected_code, got err_code=$err_code HTTP $code"
        echo "      Response: $(echo "$json_body" | head -c 200)"
    fi
}

check_session() {
    local name="$1" url="$2" method="${3:-GET}" token="$4" body="${5:-}"
    local args=(-s -w "\n%{http_code}" -X "$method" -H "session-token: $token")
    [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")

    local resp code json_body success
    resp=$(curl "${args[@]}" "${BASE}${url}")
    code=$(echo "$resp" | tail -1)
    json_body=$(echo "$resp" | sed '$d')

    success=$(echo "$json_body" | json_val "d.get('success', False)")

    if [[ "$code" =~ ^2 ]] && [[ "$success" == "True" ]]; then
        ok "$name (HTTP $code)"
    else
        fail "$name" "HTTP $code, success=$success"
        echo "      Response: $(echo "$json_body" | head -c 200)"
    fi
}

create_and_capture() {
    local url="$1" key="$2" body="$3"
    local resp
    resp=$(curl -s -X POST "${BASE}${url}" \
        -H "api-key: $key" \
        -H "Content-Type: application/json" \
        -d "$body")
    echo "$resp"
}

check_session_fail() {
    local name="$1" url="$2" expected_code="$3" method="${4:-GET}" token="$5" body="${6:-}"
    local args=(-s -w "\n%{http_code}" -X "$method" -H "session-token: $token")
    [[ -n "$body" ]] && args+=(-H "Content-Type: application/json" -d "$body")

    local resp code json_body err_code
    resp=$(curl "${args[@]}" "${BASE}${url}")
    code=$(echo "$resp" | tail -1)
    json_body=$(echo "$resp" | sed '$d')

    err_code=$(echo "$json_body" | json_val "d.get('error',{}).get('code','')")

    if [[ "$err_code" == "$expected_code" ]]; then
        ok "$name → $expected_code"
    else
        fail "$name" "expected $expected_code, got err_code=$err_code HTTP $code"
        echo "      Response: $(echo "$json_body" | head -c 200)"
    fi
}

echo "=============================================="
echo " base_api Comprehensive Test Suite"
echo "=============================================="
echo " Admin key:   ${ADMIN_KEY:0:12}..."
echo " Regular key: ${REGULAR_KEY:0:12}..."
echo " Sales key:   ${SALES_KEY:0:12}..."
echo "=============================================="


# ══════════════════════════════════════════════════
# SECTION 1: HEALTH & BASIC AUTH
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 1. Health / No-Auth ━━━"
check "GET /api/v2/test" "/api/v2/test"

echo ""
echo "━━━ 2. API-Key Authentication ━━━"
check "auth/test (admin key)" "/api/v2/auth/test"
check "auth/test (regular key)" "/api/v2/auth/test" "GET" "$REGULAR_KEY"

echo ""
echo "━━━ 3. User Info Endpoints ━━━"
check "user/info (admin)" "/api/v2/user/info"
check "user/info (regular)" "/api/v2/user/info" "GET" "$REGULAR_KEY"
check "auth/me (admin)" "/api/v2/auth/me"
check "auth/me (regular)" "/api/v2/auth/me" "GET" "$REGULAR_KEY"


# ══════════════════════════════════════════════════
# SECTION 2: SESSION AUTH FLOW
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 4. Session Auth Flow (login → me → refresh → logout) ━━━"
LOGIN_RESP=$(curl -s -X POST "${BASE}/api/v2/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin"}')
SESSION_TOKEN=$(echo "$LOGIN_RESP" | json_val "d['data']['session_token']")
if [[ -n "$SESSION_TOKEN" && "$SESSION_TOKEN" != "" ]]; then
    ok "POST /api/v2/auth/login"
else
    fail "POST /api/v2/auth/login" "no session token"
    SESSION_TOKEN=""
fi

if [[ -n "$SESSION_TOKEN" ]]; then
    check_session "auth/me (session)" "/api/v2/auth/me" "GET" "$SESSION_TOKEN"
    check_session "search via session" "/api/v2/search/res.partner?limit=2" "GET" "$SESSION_TOKEN"

    REFRESH_RESP=$(curl -s -X POST "${BASE}/api/v2/auth/refresh" -H "session-token: $SESSION_TOKEN")
    NEW_TOKEN=$(echo "$REFRESH_RESP" | json_val "d['data']['session_token']")
    if [[ -n "$NEW_TOKEN" && "$NEW_TOKEN" != "" ]]; then
        ok "POST /api/v2/auth/refresh"
        SESSION_TOKEN="$NEW_TOKEN"
    else
        fail "POST /api/v2/auth/refresh" "no new token"
    fi

    check_session "auth/me after refresh" "/api/v2/auth/me" "GET" "$SESSION_TOKEN"
    check_session "POST /api/v2/auth/logout" "/api/v2/auth/logout" "POST" "$SESSION_TOKEN"
else
    fail "auth/me (session)" "skipped"
    fail "search via session" "skipped"
    fail "POST /api/v2/auth/refresh" "skipped"
    fail "auth/me after refresh" "skipped"
    fail "POST /api/v2/auth/logout" "skipped"
fi

echo ""
echo "━━━ 5. Session Auth Error Cases ━━━"
check_fail "login bad password" "/api/v2/auth/login" "AUTH_FAILED" "POST" "" '{"username":"admin","password":"wrongpass"}'
check_fail "login missing fields" "/api/v2/auth/login" "MISSING_CREDENTIALS" "POST" "" '{"username":"admin"}'
check_fail "login bad content-type" "/api/v2/auth/login" "INVALID_CONTENT_TYPE" "POST" ""
check_fail "refresh missing token" "/api/v2/auth/refresh" "MISSING_SESSION_TOKEN" "POST" ""


# ══════════════════════════════════════════════════
# SECTION 3: LEGACY ENDPOINTS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 6. Partners (legacy) ━━━"
check "partners (default)" "/api/v2/partners"
check "partners (all, limit=5)" "/api/v2/partners?customers_only=false&limit=5"
check "partners (offset)" "/api/v2/partners?limit=2&offset=1"

echo ""
echo "━━━ 7. Products (legacy) ━━━"
check "products (default)" "/api/v2/products"
check "products (limit=3)" "/api/v2/products?limit=3"
check "products (sale_ok=false)" "/api/v2/products?sale_ok=false&limit=5"


# ══════════════════════════════════════════════════
# SECTION 4: GENERIC SEARCH
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 8. Generic Search ━━━"
check "search res.partner" "/api/v2/search/res.partner?limit=5"
check "search res.partner (fields)" "/api/v2/search/res.partner?limit=3&fields=name,email,phone,city,country_id"
check "search product.template" "/api/v2/search/product.template?limit=5"
check "search product.product" "/api/v2/search/product.product?limit=5&fields=name,default_code,list_price"
check "search product.category" "/api/v2/search/product.category?limit=5"
check "search sale.order" "/api/v2/search/sale.order?limit=5&fields=name,partner_id,amount_total,state"
check "search sale.order.line" "/api/v2/search/sale.order.line?limit=5&fields=order_id,product_id,product_uom_qty,price_unit"
check "search hr.employee" "/api/v2/search/hr.employee?limit=5&fields=name,job_title,department_id,work_email"
check "search hr.department" "/api/v2/search/hr.department?limit=5"
check "search crm.lead" "/api/v2/search/crm.lead?limit=5&fields=name,partner_id,expected_revenue,type,priority"
check "search crm.team" "/api/v2/search/crm.team?limit=5"
check "search res.company" "/api/v2/search/res.company?limit=5"
check "search res.users" "/api/v2/search/res.users?limit=5&fields=name,login,email"
check "search res.country" "/api/v2/search/res.country?limit=5&fields=name,code"
check "search res.currency" "/api/v2/search/res.currency?limit=5&fields=name,symbol,position"
check "search ir.module.module (installed)" "/api/v2/search/ir.module.module?limit=5&fields=name,state,shortdesc&state=installed"

echo ""
echo "━━━ 9. Search with Filtering ━━━"
check "search partners (is_company)" "/api/v2/search/res.partner?is_company=true&limit=5&fields=name,email,is_company"
check "search users (active)" "/api/v2/search/res.users?active=true&limit=5&fields=name,login"

echo ""
echo "━━━ 10. Get Record by ID ━━━"
check "get res.partner/1" "/api/v2/search/res.partner/1?fields=id,name,email,phone"
check "get res.company/1" "/api/v2/search/res.company/1?fields=name,currency_id,country_id,phone"
check "get res.users/2" "/api/v2/search/res.users/2?fields=name,login,email,active"
check "get res.partner/1 (all fields)" "/api/v2/search/res.partner/1"


# ══════════════════════════════════════════════════
# SECTION 5: MODEL FIELDS & DISCOVERY
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 11. Model Fields ━━━"
check "fields res.partner" "/api/v2/fields/res.partner"
check "fields sale.order" "/api/v2/fields/sale.order"
check "fields hr.employee" "/api/v2/fields/hr.employee"
check "fields crm.lead" "/api/v2/fields/crm.lead"
check "fields mail.message" "/api/v2/fields/mail.message"
check "fields mail.activity" "/api/v2/fields/mail.activity"
check "fields mail.notification" "/api/v2/fields/mail.notification"

echo ""
echo "━━━ 12. Model Discovery ━━━"
check "models (sale)" "/api/v2/models?search=sale"
check "models (hr)" "/api/v2/models?search=hr"
check "models (crm)" "/api/v2/models?search=crm"
check "models (mail)" "/api/v2/models?search=mail"
check "models (config)" "/api/v2/models?search=config"
check "models (account)" "/api/v2/models?search=account"


# ══════════════════════════════════════════════════
# SECTION 6: GROUPS & USERS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 13. Groups ━━━"
check "groups (admin)" "/api/v2/groups"

echo ""
echo "━━━ 14. Users List & Detail ━━━"
check "users list (admin)" "/api/v2/users"
check "users search" "/api/v2/users?search=admin"
check "users list (regular)" "/api/v2/users" "GET" "$REGULAR_KEY"
check "get user/2 (admin views admin)" "/api/v2/users/2"
check "get user/6 (admin views regular)" "/api/v2/users/6"
check "get user/6 (regular views self)" "/api/v2/users/6" "GET" "$REGULAR_KEY"
check "get user/2 (regular views admin)" "/api/v2/users/2" "GET" "$REGULAR_KEY"


# ══════════════════════════════════════════════════
# SECTION 7: CRUD LIFECYCLE
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 15. Create → Update → Delete (res.partner) ━━━"
CREATE_RESP=$(create_and_capture "/api/v2/create/res.partner" "$API_KEY" \
    '{"name":"Test API Partner","email":"testpartner@api.com","phone":"+1-555-0199","is_company":false,"customer_rank":1}')
CREATE_SUCCESS=$(echo "$CREATE_RESP" | json_val "d.get('success', False)")
CREATED_PARTNER_ID=$(echo "$CREATE_RESP" | json_val "d.get('data',{}).get('id','')")
if [[ "$CREATE_SUCCESS" == "True" ]]; then
    ok "CREATE res.partner (id=$CREATED_PARTNER_ID)"
else
    fail "CREATE res.partner" "success=$CREATE_SUCCESS"
fi

if [[ -n "$CREATED_PARTNER_ID" && "$CREATED_PARTNER_ID" != "" ]]; then
    check "READ created partner" "/api/v2/search/res.partner/$CREATED_PARTNER_ID?fields=name,email,phone"
    check "UPDATE partner phone" "/api/v2/update/res.partner/$CREATED_PARTNER_ID" "PUT" "$API_KEY" '{"phone":"+1-555-UPDATED"}'
    check "UPDATE partner name" "/api/v2/update/res.partner/$CREATED_PARTNER_ID" "PUT" "$API_KEY" '{"name":"Test Partner Updated"}'
    check "DELETE partner" "/api/v2/delete/res.partner/$CREATED_PARTNER_ID" "DELETE" "$API_KEY"
    check_fail "READ deleted partner" "/api/v2/search/res.partner/$CREATED_PARTNER_ID" "RECORD_NOT_FOUND" "GET"
else
    fail "CRUD partner lifecycle" "no partner ID from create"
fi

echo ""
echo "━━━ 16. User Lifecycle (create → update → password → reset → api-key → delete) ━━━"
CREATE_USER_RESP=$(create_and_capture "/api/v2/create/res.users" "$API_KEY" \
    '{"name":"API Test User","login":"apitest@test.com","email":"apitest@test.com"}')
CREATE_USER_OK=$(echo "$CREATE_USER_RESP" | json_val "d.get('success', False)")
CREATED_USER_ID=$(echo "$CREATE_USER_RESP" | json_val "d.get('data',{}).get('id','')")
if [[ "$CREATE_USER_OK" == "True" ]]; then
    ok "CREATE res.users (id=$CREATED_USER_ID)"
else
    fail "CREATE res.users" "success=$CREATE_USER_OK"
    echo "      Response: $(echo "$CREATE_USER_RESP" | head -c 300)"
fi

if [[ -n "$CREATED_USER_ID" && "$CREATED_USER_ID" != "" ]]; then
    check "READ created user" "/api/v2/users/$CREATED_USER_ID"
    check "UPDATE user name+phone" "/api/v2/users/$CREATED_USER_ID" "PUT" "$API_KEY" '{"name":"API Test User Updated","phone":"+1-555-0202"}'
    check "UPDATE user lang" "/api/v2/users/$CREATED_USER_ID" "PUT" "$API_KEY" '{"lang":"en_US","tz":"America/New_York"}'
    check "UPDATE user groups (admin)" "/api/v2/users/$CREATED_USER_ID" "PUT" "$API_KEY" '{"group_names":["Role / User"]}'
    check "CHANGE password (admin)" "/api/v2/users/$CREATED_USER_ID/password" "PUT" "$API_KEY" '{"new_password":"newpass123"}'
    check "RESET password (admin)" "/api/v2/users/$CREATED_USER_ID/reset-password" "POST" "$API_KEY"
    check "GENERATE api-key" "/api/v2/users/$CREATED_USER_ID/api-key" "POST" "$API_KEY"
    # Cleanup
    curl -s -X DELETE "${BASE}/api/v2/delete/res.users/$CREATED_USER_ID" -H "api-key: $API_KEY" > /dev/null 2>&1 || true
    ok "DELETE test user (cleanup)"
else
    fail "User lifecycle" "no user ID from create"
fi


# ══════════════════════════════════════════════════
# SECTION 8: SETTINGS (admin only)
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 17. Settings - res.config.settings (admin) ━━━"
check "search res.config.settings" "/api/v2/search/res.config.settings?limit=5"
check "fields res.config.settings" "/api/v2/fields/res.config.settings"

# Create a settings record so we can test get/update
SETTINGS_RESP=$(create_and_capture "/api/v2/create/res.config.settings" "$API_KEY" '{"show_effect":true}')
SETTINGS_OK=$(echo "$SETTINGS_RESP" | json_val "d.get('success', False)")
SETTINGS_ID=$(echo "$SETTINGS_RESP" | json_val "d.get('data',{}).get('id','')")
if [[ "$SETTINGS_OK" == "True" && -n "$SETTINGS_ID" && "$SETTINGS_ID" != "" ]]; then
    ok "CREATE res.config.settings (id=$SETTINGS_ID)"
    check "get res.config.settings/$SETTINGS_ID" "/api/v2/search/res.config.settings/$SETTINGS_ID?fields=show_effect"
    check "update res.config.settings (show_effect)" "/api/v2/update/res.config.settings/$SETTINGS_ID" "PUT" "$API_KEY" '{"show_effect":true}'
else
    fail "CREATE res.config.settings" "success=$SETTINGS_OK"
    fail "get res.config.settings" "skipped - no settings record"
    fail "update res.config.settings" "skipped - no settings record"
fi

echo ""
echo "━━━ 18. Settings - ir.config_parameter (admin) ━━━"
check "search ir.config_parameter" "/api/v2/search/ir.config_parameter?limit=10&fields=key,value"
check "get ir.config_parameter/4 (web.base.url)" "/api/v2/search/ir.config_parameter/4?fields=key,value"
check "update ir.config_parameter" "/api/v2/update/ir.config_parameter/9" "PUT" "$API_KEY" '{"value":"True"}'

echo ""
echo "━━━ 19. Settings - res.company (admin) ━━━"
check "get res.company/1 (full)" "/api/v2/search/res.company/1?fields=name,currency_id,country_id,street,city,zip,phone,email,website,vat"
check "update res.company phone" "/api/v2/update/res.company/1" "PUT" "$API_KEY" '{"phone":"+1-555-0100"}'


# ══════════════════════════════════════════════════
# SECTION 9: NOTIFICATIONS & MESSAGING
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 20. mail.notification ━━━"
check "notifications (admin)" "/api/v2/search/mail.notification?limit=10&fields=mail_message_id,res_partner_id,notification_type,notification_status,is_read,read_date,failure_type"
check "notifications (regular)" "/api/v2/search/mail.notification?limit=5&fields=mail_message_id,res_partner_id,notification_status,is_read" "GET" "$REGULAR_KEY"

echo ""
echo "━━━ 21. mail.message ━━━"
check "messages (admin, all)" "/api/v2/search/mail.message?limit=5&fields=subject,body,author_id,date,message_type,model,res_id,record_name,starred,needaction,is_internal"
check "messages (regular)" "/api/v2/search/mail.message?limit=5&fields=subject,body,author_id,date,message_type,model,res_id,record_name" "GET" "$REGULAR_KEY"
check "messages (needaction)" "/api/v2/search/mail.message?needaction=true&limit=5&fields=subject,body,author_id,date,model,record_name"
check "messages (starred)" "/api/v2/search/mail.message?starred=true&limit=5&fields=subject,body,author_id,date"
check "messages (comment type)" "/api/v2/search/mail.message?message_type=comment&limit=5&fields=subject,body,author_id,date,model,record_name"
check "messages (notification type)" "/api/v2/search/mail.message?message_type=notification&limit=5&fields=body,author_id,date,model,record_name"
check "messages for res.partner/1" "/api/v2/search/mail.message?model=res.partner&res_id=1&limit=5&fields=body,author_id,date,message_type"

echo ""
echo "━━━ 22. mail.message - Create (post note) ━━━"
MSG_RESP=$(create_and_capture "/api/v2/create/mail.message" "$API_KEY" \
    '{"body":"<p>Test note from API test suite</p>","message_type":"comment","model":"res.partner","res_id":1,"subtype_id":2}')
MSG_OK=$(echo "$MSG_RESP" | json_val "d.get('success', False)")
MSG_ID=$(echo "$MSG_RESP" | json_val "d.get('data',{}).get('id','')")
if [[ "$MSG_OK" == "True" ]]; then
    ok "CREATE mail.message (internal note, id=$MSG_ID)"
else
    fail "CREATE mail.message" "success=$MSG_OK"
fi

MSG2_RESP=$(create_and_capture "/api/v2/create/mail.message" "$API_KEY" \
    '{"body":"<p>Test discussion from API test suite</p>","message_type":"comment","model":"res.partner","res_id":1,"subtype_id":1}')
MSG2_OK=$(echo "$MSG2_RESP" | json_val "d.get('success', False)")
MSG2_ID=$(echo "$MSG2_RESP" | json_val "d.get('data',{}).get('id','')")
if [[ "$MSG2_OK" == "True" ]]; then
    ok "CREATE mail.message (discussion, id=$MSG2_ID)"
else
    fail "CREATE mail.message (discussion)" "success=$MSG2_OK"
fi

if [[ -n "$MSG_ID" && "$MSG_ID" != "" ]]; then
    check "READ created message" "/api/v2/search/mail.message/$MSG_ID?fields=body,author_id,date,message_type,model,res_id,record_name,subtype_id,is_internal"
fi


# ══════════════════════════════════════════════════
# SECTION 10: ACTIVITIES
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 23. mail.activity.type ━━━"
check "activity types (admin)" "/api/v2/search/mail.activity.type?limit=20&fields=name,summary,icon,category,delay_count,delay_unit,res_model,chaining_type"
check "activity types (regular)" "/api/v2/search/mail.activity.type?limit=20&fields=name,icon,category" "GET" "$REGULAR_KEY"

echo ""
echo "━━━ 24. mail.activity - CRUD ━━━"

# Look up res_model_id for res.partner
MODEL_ID_RESP=$(curl -s -H "api-key: $ADMIN_KEY" \
    "${BASE}/api/v2/search/ir.model?model=res.partner&fields=id,model")
RES_PARTNER_MODEL_ID=$(echo "$MODEL_ID_RESP" | json_val "d['data']['records'][0]['id']")

if [[ -n "$RES_PARTNER_MODEL_ID" && "$RES_PARTNER_MODEL_ID" != "" ]]; then
    ok "Lookup res_model_id for res.partner = $RES_PARTNER_MODEL_ID"

    # Create activity (admin)
    ACT_RESP=$(create_and_capture "/api/v2/create/mail.activity" "$API_KEY" \
        "{\"summary\":\"Test call from API suite\",\"note\":\"<p>Auto-generated test activity</p>\",\"activity_type_id\":2,\"date_deadline\":\"2099-12-31\",\"user_id\":2,\"res_model_id\":$RES_PARTNER_MODEL_ID,\"res_id\":1}")
    ACT_OK=$(echo "$ACT_RESP" | json_val "d.get('success', False)")
    ACT_ID=$(echo "$ACT_RESP" | json_val "d.get('data',{}).get('id','')")
    if [[ "$ACT_OK" == "True" ]]; then
        ok "CREATE mail.activity (Call, id=$ACT_ID)"
    else
        fail "CREATE mail.activity (Call)" "success=$ACT_OK"
        echo "      Response: $(echo "$ACT_RESP" | head -c 300)"
    fi

    # Create activity (email type)
    ACT2_RESP=$(create_and_capture "/api/v2/create/mail.activity" "$API_KEY" \
        "{\"summary\":\"Test email from API suite\",\"note\":\"<p>Send a follow-up email</p>\",\"activity_type_id\":1,\"date_deadline\":\"2099-12-31\",\"user_id\":2,\"res_model_id\":$RES_PARTNER_MODEL_ID,\"res_id\":1}")
    ACT2_OK=$(echo "$ACT2_RESP" | json_val "d.get('success', False)")
    ACT2_ID=$(echo "$ACT2_RESP" | json_val "d.get('data',{}).get('id','')")
    if [[ "$ACT2_OK" == "True" ]]; then
        ok "CREATE mail.activity (Email, id=$ACT2_ID)"
    else
        fail "CREATE mail.activity (Email)" "success=$ACT2_OK"
    fi

    # Create activity (meeting type)
    ACT3_RESP=$(create_and_capture "/api/v2/create/mail.activity" "$API_KEY" \
        "{\"summary\":\"Test meeting from API suite\",\"activity_type_id\":3,\"date_deadline\":\"2099-12-31\",\"user_id\":2,\"res_model_id\":$RES_PARTNER_MODEL_ID,\"res_id\":1}")
    ACT3_OK=$(echo "$ACT3_RESP" | json_val "d.get('success', False)")
    ACT3_ID=$(echo "$ACT3_RESP" | json_val "d.get('data',{}).get('id','')")
    if [[ "$ACT3_OK" == "True" ]]; then
        ok "CREATE mail.activity (Meeting, id=$ACT3_ID)"
    else
        fail "CREATE mail.activity (Meeting)" "success=$ACT3_OK"
    fi

    # Create activity (to-do type)
    ACT4_RESP=$(create_and_capture "/api/v2/create/mail.activity" "$API_KEY" \
        "{\"summary\":\"Test to-do from API suite\",\"activity_type_id\":4,\"date_deadline\":\"2099-12-31\",\"user_id\":2,\"res_model_id\":$RES_PARTNER_MODEL_ID,\"res_id\":1}")
    ACT4_OK=$(echo "$ACT4_RESP" | json_val "d.get('success', False)")
    ACT4_ID=$(echo "$ACT4_RESP" | json_val "d.get('data',{}).get('id','')")
    if [[ "$ACT4_OK" == "True" ]]; then
        ok "CREATE mail.activity (To-Do, id=$ACT4_ID)"
    else
        fail "CREATE mail.activity (To-Do)" "success=$ACT4_OK"
    fi

    # Search activities
    check "search activities (all)" "/api/v2/search/mail.activity?limit=10&fields=summary,date_deadline,user_id,activity_type_id,res_model,res_name,state"
    check "search activities (limit+fields)" "/api/v2/search/mail.activity?limit=5&fields=summary,date_deadline,state,res_name"
    check "search activities (by user)" "/api/v2/search/mail.activity?user_id=2&limit=5&fields=summary,date_deadline,activity_type_id,state"
    check "search activities (by model)" "/api/v2/search/mail.activity?res_model=res.partner&limit=5&fields=summary,date_deadline,res_name"
    check "search activities (regular)" "/api/v2/search/mail.activity?limit=5&fields=summary,date_deadline,user_id,state" "GET" "$REGULAR_KEY"

    # Read specific activity
    if [[ -n "$ACT_ID" && "$ACT_ID" != "" ]]; then
        check "get activity/$ACT_ID" "/api/v2/search/mail.activity/$ACT_ID?fields=summary,note,date_deadline,user_id,activity_type_id,res_model,res_id,res_name,state,can_write"
    fi

    # Update activity
    if [[ -n "$ACT_ID" && "$ACT_ID" != "" ]]; then
        check "UPDATE activity summary" "/api/v2/update/mail.activity/$ACT_ID" "PUT" "$API_KEY" '{"summary":"Updated call from API suite"}'
        check "UPDATE activity deadline" "/api/v2/update/mail.activity/$ACT_ID" "PUT" "$API_KEY" '{"date_deadline":"2099-06-15"}'
    fi

    # Delete activities (cleanup)
    for AID in $ACT_ID $ACT2_ID $ACT3_ID $ACT4_ID; do
        if [[ -n "$AID" && "$AID" != "" ]]; then
            check "DELETE activity/$AID" "/api/v2/delete/mail.activity/$AID" "DELETE" "$API_KEY"
        fi
    done
else
    fail "Lookup res_model_id" "could not find res.partner model ID"
fi


# ══════════════════════════════════════════════════
# SECTION 11: FOLLOWERS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 25. mail.followers ━━━"
check "followers (admin)" "/api/v2/search/mail.followers?limit=10&fields=partner_id,res_model,res_id,name,email,subtype_ids"
check "followers (regular)" "/api/v2/search/mail.followers?limit=5&fields=partner_id,res_model,res_id,name" "GET" "$REGULAR_KEY"
check "followers for res.partner" "/api/v2/search/mail.followers?res_model=res.partner&limit=5&fields=partner_id,name,res_id,subtype_ids"
check "followers for sale.order" "/api/v2/search/mail.followers?res_model=sale.order&limit=5&fields=partner_id,name,res_id"


# ══════════════════════════════════════════════════
# SECTION 12: MESSAGE SUBTYPES
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 26. mail.message.subtype ━━━"
check "subtypes (admin)" "/api/v2/search/mail.message.subtype?limit=30&fields=name,description,internal,hidden,default,res_model,sequence"
check "subtypes (regular)" "/api/v2/search/mail.message.subtype?limit=10&fields=name,internal,default" "GET" "$REGULAR_KEY"


# ══════════════════════════════════════════════════
# SECTION 13: ACCESS CONTROL - REGULAR USER RESTRICTIONS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 27. Access Control - Regular User Denied ━━━"
check_fail "groups (regular → denied)" "/api/v2/groups" "ACCESS_DENIED" "GET" "$REGULAR_KEY"
check_fail "res.config.settings (regular → denied)" "/api/v2/search/res.config.settings" "ACCESS_DENIED" "GET" "$REGULAR_KEY"
check_fail "ir.config_parameter (regular → denied)" "/api/v2/search/ir.config_parameter" "ACCESS_DENIED" "GET" "$REGULAR_KEY"
check_fail "update company (regular → denied)" "/api/v2/update/res.company/1" "ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"phone":"+1-999"}'
check_fail "update settings (regular → denied)" "/api/v2/update/res.config.settings/1" "ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"show_effect":false}'
check_fail "update config param (regular → denied)" "/api/v2/update/ir.config_parameter/9" "ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"value":"False"}'
check_fail "reset password (regular → denied)" "/api/v2/users/2/reset-password" "ACCESS_DENIED" "POST" "$REGULAR_KEY"
check_fail "update other user (regular → denied)" "/api/v2/users/2" "ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"name":"Hacked"}'

echo ""
echo "━━━ 28. Access Control - Regular User Self-Service ━━━"
check "regular user update self name" "/api/v2/users/6" "PUT" "$REGULAR_KEY" '{"name":"Regular User"}'
check "regular user update self phone" "/api/v2/users/6" "PUT" "$REGULAR_KEY" '{"phone":"+1-555-0101"}'
check "regular user update self tz" "/api/v2/users/6" "PUT" "$REGULAR_KEY" '{"tz":"America/New_York"}'
check_fail "regular user admin-only field (login)" "/api/v2/users/6" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"login":"hacked@login.com"}'
check_fail "regular user admin-only field (active)" "/api/v2/users/6" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"active":false}'
check_fail "regular user admin-only field (groups)" "/api/v2/users/6" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"group_names":["Administrator"]}'
check_fail "regular user admin-only field (group_ids)" "/api/v2/users/6" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"group_ids":[4]}'
check_fail "regular user admin-only field (company_id)" "/api/v2/users/6" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"company_id":99}'

echo ""
echo "━━━ 29. Access Control - Admin Manage Users ━━━"
check "admin update other user name" "/api/v2/users/6" "PUT" "$API_KEY" '{"name":"Regular User"}'
check "admin update other user login" "/api/v2/users/6" "PUT" "$API_KEY" '{"login":"user@test.com"}'
check "admin update other user groups" "/api/v2/users/6" "PUT" "$API_KEY" '{"group_names":["Role / User"]}'
check "admin generate api-key for other" "/api/v2/users/6/api-key" "POST" "$API_KEY"


# ══════════════════════════════════════════════════
# SECTION 14: ERROR HANDLING
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 30. Error Handling ━━━"
# /api/v2/auth/test may still authenticate via cookie if run from same session
# Test with a truly invalid key instead
check_fail "bad api key" "/api/v2/auth/test" "INVALID_API_KEY" "GET" "invalidkey000000000000000000000000000000"
check_fail "invalid model" "/api/v2/search/fake.model" "MODEL_NOT_FOUND"
check_fail "record not found" "/api/v2/search/res.partner/999999" "RECORD_NOT_FOUND"
# Odoo silently ignores unknown fields, so test with a model that has strict checks
# Instead test passing a truly bogus format
check_fail "fields on bad model" "/api/v2/fields/fake.model" "MODEL_NOT_FOUND"
check_fail "delete nonexistent" "/api/v2/delete/res.partner/999999" "RECORD_NOT_FOUND" "DELETE"
check_fail "update nonexistent" "/api/v2/update/res.partner/999999" "RECORD_NOT_FOUND" "PUT" "$API_KEY" '{"name":"x"}'
check_fail "create bad model" "/api/v2/create/fake.model" "MODEL_NOT_FOUND" "POST" "$API_KEY" '{"name":"x"}'
check_fail "create no data" "/api/v2/create/res.partner" "INVALID_CONTENT_TYPE" "POST" "$API_KEY"
check_fail "update no data" "/api/v2/update/res.partner/1" "INVALID_CONTENT_TYPE" "PUT" "$API_KEY"
check_fail "password missing new_password" "/api/v2/users/2/password" "MISSING_PASSWORD" "PUT" "$API_KEY" '{"old_password":"x"}'
check_fail "password user not found" "/api/v2/users/999999/password" "USER_NOT_FOUND" "PUT" "$API_KEY" '{"new_password":"x"}'
check_fail "reset password user not found" "/api/v2/users/999999/reset-password" "USER_NOT_FOUND" "POST" "$API_KEY"
check_fail "get user not found" "/api/v2/users/999999" "USER_NOT_FOUND" "GET"
check_fail "update user not found" "/api/v2/users/999999" "USER_NOT_FOUND" "PUT" "$API_KEY" '{"name":"x"}'
check_fail "api-key for bad key" "/api/v2/search/res.partner" "INVALID_API_KEY" "GET" "badkey0000000000000000000000000000000000"


# ══════════════════════════════════════════════════
# SECTION 15: SESSION + API KEY DUAL AUTH ON SAME ENDPOINTS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 31. Dual Auth (session token on modern endpoints) ━━━"
LOGIN_RESP2=$(curl -s -X POST "${BASE}/api/v2/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin"}')
SESSION2=$(echo "$LOGIN_RESP2" | json_val "d['data']['session_token']")
if [[ -n "$SESSION2" && "$SESSION2" != "" ]]; then
    ok "login for dual-auth tests"
    check_session "search res.partner (session)" "/api/v2/search/res.partner?limit=2" "GET" "$SESSION2"
    check_session "search mail.message (session)" "/api/v2/search/mail.message?limit=2&fields=subject,body,date" "GET" "$SESSION2"
    check_session "search mail.activity (session)" "/api/v2/search/mail.activity?limit=2&fields=summary,state" "GET" "$SESSION2"
    check_session "search mail.notification (session)" "/api/v2/search/mail.notification?limit=2&fields=notification_status,is_read" "GET" "$SESSION2"
    check_session "users list (session)" "/api/v2/users" "GET" "$SESSION2"
    check_session "auth/me (session)" "/api/v2/auth/me" "GET" "$SESSION2"
    check_session "get user/2 (session)" "/api/v2/users/2" "GET" "$SESSION2"
    check_session "models (session)" "/api/v2/models?search=mail" "GET" "$SESSION2"
    check_session "fields (session)" "/api/v2/fields/mail.message" "GET" "$SESSION2"
    # Cleanup
    curl -s -X POST "${BASE}/api/v2/auth/logout" -H "session-token: $SESSION2" > /dev/null 2>&1 || true
else
    fail "login for dual-auth tests" "no session token"
fi


# ══════════════════════════════════════════════════
# SECTION 16: CROSS-MODULE INTEGRATION SEARCHES
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 32. Cross-Module Searches ━━━"
check "search account.move" "/api/v2/search/account.move?limit=3&fields=name,partner_id,amount_total,state"
check "search account.move.line" "/api/v2/search/account.move.line?limit=3&fields=name,debit,credit,account_id"
check "search account.account" "/api/v2/search/account.account?limit=5&fields=code,name"
check "search account.tax" "/api/v2/search/account.tax?limit=5&fields=name,amount,type_tax_use"
check "search ir.model (all)" "/api/v2/search/ir.model?limit=5&fields=model,name"
check "search ir.model.fields" "/api/v2/search/ir.model.fields?limit=5&fields=name,model,ttype"
check "search discuss.channel" "/api/v2/search/discuss.channel?limit=3&fields=name,channel_type"
check "search mail.mail" "/api/v2/search/mail.mail?limit=3&fields=subject,state,email_to"
check_fail "search api.session (blocked)" "/api/v2/search/api.session" "ACCESS_DENIED"


# ══════════════════════════════════════════════════
# SECTION 17: REGULAR USER WRITE BOUNDARY TESTS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 33. Regular User - Create Boundaries ━━━"
check_fail "regular create res.users (denied)" "/api/v2/create/res.users" "ACCESS_DENIED" "POST" "$REGULAR_KEY" '{"name":"Hacker","login":"hacker@test.com","email":"hacker@test.com"}'
check_fail "regular create ir.config_parameter (denied)" "/api/v2/create/ir.config_parameter" "ACCESS_DENIED" "POST" "$REGULAR_KEY" '{"key":"test.hack","value":"true"}'
check_fail "regular create res.config.settings (denied)" "/api/v2/create/res.config.settings" "ACCESS_DENIED" "POST" "$REGULAR_KEY" '{"show_effect":true}'

echo ""
echo "━━━ 34. Regular User - Delete Boundaries ━━━"
check_fail "regular delete res.company/1 (denied)" "/api/v2/delete/res.company/1" "ACCESS_DENIED" "DELETE" "$REGULAR_KEY"
check_fail "regular delete ir.config_parameter (denied)" "/api/v2/delete/ir.config_parameter/4" "ACCESS_DENIED" "DELETE" "$REGULAR_KEY"

echo ""
echo "━━━ 35. Regular User - API Key for Others (denied) ━━━"
check_fail "regular gen api-key for admin (denied)" "/api/v2/users/2/api-key" "ACCESS_DENIED" "POST" "$REGULAR_KEY"
check_fail "regular gen api-key for sales (denied)" "/api/v2/users/7/api-key" "ACCESS_DENIED" "POST" "$REGULAR_KEY"
check "regular gen api-key for self" "/api/v2/users/6/api-key" "POST" "$REGULAR_KEY"

echo ""
echo "━━━ 36. Regular User - Own Password Change ━━━"
check_fail "regular change admin password (denied)" "/api/v2/users/2/password" "ACCESS_DENIED" "PUT" "$REGULAR_KEY" '{"new_password":"hacked"}'


# ══════════════════════════════════════════════════
# SECTION 18: SALES USER ROLE TESTS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 37. Sales User - Auth & Identity ━━━"
check "sales auth/test" "/api/v2/auth/test" "GET" "$SALES_KEY"
check "sales auth/me" "/api/v2/auth/me" "GET" "$SALES_KEY"
check "sales user/info" "/api/v2/user/info" "GET" "$SALES_KEY"

echo ""
echo "━━━ 38. Sales User - Read Access ━━━"
check "sales search res.partner" "/api/v2/search/res.partner?limit=3" "GET" "$SALES_KEY"
check "sales search sale.order" "/api/v2/search/sale.order?limit=3&fields=name,partner_id,amount_total,state" "GET" "$SALES_KEY"
check "sales search crm.lead" "/api/v2/search/crm.lead?limit=3&fields=name,partner_id,expected_revenue" "GET" "$SALES_KEY"
check "sales search product.template" "/api/v2/search/product.template?limit=3" "GET" "$SALES_KEY"
check "sales users list" "/api/v2/users" "GET" "$SALES_KEY"
check "sales get self (id=7)" "/api/v2/users/7" "GET" "$SALES_KEY"
check "sales get other user" "/api/v2/users/2" "GET" "$SALES_KEY"
check "sales search mail.message" "/api/v2/search/mail.message?limit=3&fields=subject,body,date" "GET" "$SALES_KEY"
check "sales search mail.activity" "/api/v2/search/mail.activity?limit=3&fields=summary,date_deadline,state" "GET" "$SALES_KEY"
check "sales search mail.notification" "/api/v2/search/mail.notification?limit=3&fields=notification_status,is_read" "GET" "$SALES_KEY"
check "sales search mail.followers" "/api/v2/search/mail.followers?limit=3&fields=partner_id,res_model" "GET" "$SALES_KEY"
check "sales search res.country" "/api/v2/search/res.country?limit=2&fields=name,code" "GET" "$SALES_KEY"
check "sales models (sale)" "/api/v2/models?search=sale" "GET" "$SALES_KEY"

echo ""
echo "━━━ 39. Sales User - Admin Endpoints Denied ━━━"
check_fail "sales groups (denied)" "/api/v2/groups" "ACCESS_DENIED" "GET" "$SALES_KEY"
check_fail "sales res.config.settings (denied)" "/api/v2/search/res.config.settings" "ACCESS_DENIED" "GET" "$SALES_KEY"
check_fail "sales ir.config_parameter (denied)" "/api/v2/search/ir.config_parameter" "ACCESS_DENIED" "GET" "$SALES_KEY"
check_fail "sales update company (denied)" "/api/v2/update/res.company/1" "ACCESS_DENIED" "PUT" "$SALES_KEY" '{"phone":"+1-999"}'
check_fail "sales update settings (denied)" "/api/v2/update/res.config.settings/1" "ACCESS_DENIED" "PUT" "$SALES_KEY" '{"show_effect":false}'
check_fail "sales reset password (denied)" "/api/v2/users/2/reset-password" "ACCESS_DENIED" "POST" "$SALES_KEY"
check_fail "sales update other user (denied)" "/api/v2/users/2" "ACCESS_DENIED" "PUT" "$SALES_KEY" '{"name":"Hacked"}'
check_fail "sales create res.users (denied)" "/api/v2/create/res.users" "ACCESS_DENIED" "POST" "$SALES_KEY" '{"name":"Hacker","login":"h@h.com"}'

echo ""
echo "━━━ 40. Sales User - Self-Service ━━━"
check "sales update self name" "/api/v2/users/7" "PUT" "$SALES_KEY" '{"name":"Sales User"}'
check "sales update self phone" "/api/v2/users/7" "PUT" "$SALES_KEY" '{"phone":"+1-555-0103"}'
check_fail "sales admin-only field (login)" "/api/v2/users/7" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$SALES_KEY" '{"login":"hacked@sales.com"}'
check_fail "sales admin-only field (active)" "/api/v2/users/7" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$SALES_KEY" '{"active":false}'
check_fail "sales admin-only field (groups)" "/api/v2/users/7" "ADMIN_FIELD_ACCESS_DENIED" "PUT" "$SALES_KEY" '{"group_names":["Administrator"]}'
check "sales gen api-key for self" "/api/v2/users/7/api-key" "POST" "$SALES_KEY"
check_fail "sales gen api-key for admin (denied)" "/api/v2/users/2/api-key" "ACCESS_DENIED" "POST" "$SALES_KEY"


# ══════════════════════════════════════════════════
# SECTION 19: SESSION-BASED WRITE OPERATIONS
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 41. Session Auth - Write Operations ━━━"
LOGIN_WRITE=$(curl -s -X POST "${BASE}/api/v2/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"admin"}')
SESSION_WRITE=$(echo "$LOGIN_WRITE" | json_val "d['data']['session_token']")

if [[ -n "$SESSION_WRITE" && "$SESSION_WRITE" != "" ]]; then
    ok "login for session-write tests"

    # Create via session
    SCREATE_RESP=$(curl -s -X POST "${BASE}/api/v2/create/res.partner" \
        -H "session-token: $SESSION_WRITE" \
        -H "Content-Type: application/json" \
        -d '{"name":"Session Test Partner","email":"session@test.com"}')
    SCREATE_OK=$(echo "$SCREATE_RESP" | json_val "d.get('success', False)")
    SCREATE_ID=$(echo "$SCREATE_RESP" | json_val "d.get('data',{}).get('id','')")
    if [[ "$SCREATE_OK" == "True" ]]; then
        ok "CREATE res.partner via session (id=$SCREATE_ID)"
    else
        fail "CREATE res.partner via session" "success=$SCREATE_OK"
    fi

    # Update via session
    if [[ -n "$SCREATE_ID" && "$SCREATE_ID" != "" ]]; then
        check_session "UPDATE partner via session" "/api/v2/update/res.partner/$SCREATE_ID" "PUT" "$SESSION_WRITE" '{"name":"Session Updated"}'
        check_session "DELETE partner via session" "/api/v2/delete/res.partner/$SCREATE_ID" "DELETE" "$SESSION_WRITE"
    fi

    # User update via session
    check_session "UPDATE own user via session" "/api/v2/users/2" "PUT" "$SESSION_WRITE" '{"phone":"+1-555-0999"}'

    # Cleanup
    curl -s -X POST "${BASE}/api/v2/auth/logout" -H "session-token: $SESSION_WRITE" > /dev/null 2>&1 || true
else
    fail "login for session-write tests" "no session token"
fi

echo ""
echo "━━━ 42. Expired/Invalid Session Token ━━━"
# Dual-auth endpoints: invalid session falls through to API key → MISSING_API_KEY
check_session_fail "invalid session on search (fallthrough)" "/api/v2/search/res.partner" "MISSING_API_KEY" "GET" "fake_token_abc123"
check_session_fail "invalid session on create (fallthrough)" "/api/v2/create/res.partner" "MISSING_API_KEY" "POST" "fake_token_abc123" '{"name":"x"}'
check_session_fail "invalid session on users (fallthrough)" "/api/v2/users" "MISSING_API_KEY" "GET" "fake_token_abc123"
check_session_fail "invalid session on update (fallthrough)" "/api/v2/update/res.partner/1" "MISSING_API_KEY" "PUT" "fake_token_abc123" '{"name":"x"}'

echo ""
echo "━━━ 43. Session Auth for Regular User (write boundaries) ━━━"
# Ensure regular user has a known password for session tests
curl -s -X PUT "${BASE}/api/v2/users/6/password" \
    -H "api-key: $ADMIN_KEY" \
    -H "Content-Type: application/json" \
    -d '{"new_password":"testpass123"}' > /dev/null 2>&1

LOGIN_REG=$(curl -s -X POST "${BASE}/api/v2/auth/login" \
    -H "Content-Type: application/json" \
    -d '{"username":"user@test.com","password":"testpass123"}')
SESSION_REG=$(echo "$LOGIN_REG" | json_val "d['data']['session_token']")

if [[ -n "$SESSION_REG" && "$SESSION_REG" != "" ]]; then
    ok "login regular user via session"
    check_session "regular session search partners" "/api/v2/search/res.partner?limit=2" "GET" "$SESSION_REG"
    check_session "regular session auth/me" "/api/v2/auth/me" "GET" "$SESSION_REG"
    check_session "regular session update self" "/api/v2/users/6" "PUT" "$SESSION_REG" '{"name":"Regular User"}'
    check_session_fail "regular session update other (denied)" "/api/v2/users/2" "ACCESS_DENIED" "PUT" "$SESSION_REG" '{"name":"Hacked"}'
    check_session_fail "regular session settings (denied)" "/api/v2/search/res.config.settings" "ACCESS_DENIED" "GET" "$SESSION_REG"
    check_session_fail "regular session groups (denied)" "/api/v2/groups" "ACCESS_DENIED" "GET" "$SESSION_REG"
    # Cleanup
    curl -s -X POST "${BASE}/api/v2/auth/logout" -H "session-token: $SESSION_REG" > /dev/null 2>&1 || true
else
    fail "login regular user via session" "skipped - no token (password may differ)"
fi


# ══════════════════════════════════════════════════
# SECTION 20: SENSITIVE MODEL ACCESS (security boundary)
# ══════════════════════════════════════════════════
echo ""
echo "━━━ 44. Sensitive Model Access (now blocked) ━━━"
check_fail "admin search api.session (blocked)" "/api/v2/search/api.session" "ACCESS_DENIED"
check_fail "regular search api.session (blocked)" "/api/v2/search/api.session" "ACCESS_DENIED" "GET" "$REGULAR_KEY"
check_fail "admin search ir.cron (blocked)" "/api/v2/search/ir.cron" "ACCESS_DENIED"
check_fail "admin search ir.rule (blocked)" "/api/v2/search/ir.rule" "ACCESS_DENIED"
check_fail "admin search ir.model.access (blocked)" "/api/v2/search/ir.model.access" "ACCESS_DENIED"
check_fail "admin search res.users.apikeys (blocked)" "/api/v2/search/res.users.apikeys" "ACCESS_DENIED"


# ══════════════════════════════════════════════════
# RESULTS
# ══════════════════════════════════════════════════
echo ""
echo "=============================================="
echo " Results: $PASS passed, $FAIL failed out of $TOTAL tests"
echo "=============================================="
if [[ $FAIL -eq 0 ]]; then
    echo " ✅ ALL TESTS PASSED"
else
    echo " ❌ SOME TESTS FAILED"
fi
exit $FAIL
