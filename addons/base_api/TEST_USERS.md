# Test Users Reference

**Password for all test users:** `Test@12345`
**Admin password:** `admin`

> Users 33-36 were created via the user-creation API during testing and may not have working passwords.

---

## User Matrix

| ID | Login | Name | Role | Module Groups | Password |
|----|-------|------|------|---------------|----------|
| 2 | `admin` | Administrator | Role / Administrator | Full system admin | `admin` |
| 5 | `manager@test.com` | Manager User | Role / User | Access Rights (ERP Manager) | `Test@12345` |
| 6 | `user@test.com` | Regular User | Role / User | _(none)_ | `Test@12345` |
| 7 | `sales@test.com` | Sales User | Role / User | Sales: Own Documents Only | `Test@12345` |
| 21 | `sales_manager@test.com` | Sales Manager | Role / User | Sales: Own + All Documents, Administrator | `Test@12345` |
| 22 | `crm@test.com` | CRM User | Role / User | CRM: Own Documents Only | `Test@12345` |
| 23 | `accounting@test.com` | Accounting User | Role / User | Invoicing | `Test@12345` |
| 24 | `accounting_mgr@test.com` | Accounting Manager | Role / User | Invoicing, Administrator | `Test@12345` |
| 25 | `hr@test.com` | HR User | Role / User | HR Officer: Manage all employees | `Test@12345` |
| 26 | `hr_manager@test.com` | HR Manager | Role / User | HR Officer + Administrator | `Test@12345` |
| 27 | `inventory@test.com` | Inventory User | Role / User | Inventory User | `Test@12345` |
| 28 | `inventory_mgr@test.com` | Inventory Manager | Role / User | Inventory User + Administrator | `Test@12345` |
| 29 | `purchase@test.com` | Purchase User | Role / User | Purchase User | `Test@12345` |
| 30 | `purchase_mgr@test.com` | Purchase Manager | Role / User | Purchase User + Administrator | `Test@12345` |
| 31 | `project@test.com` | Project User | Role / User | Project User | `Test@12345` |
| 32 | `project_mgr@test.com` | Project Manager | Role / User | Project User + Administrator | `Test@12345` |
| 33 | `testfullname@test.com` | Test User FullName | Role / User | _(none)_ | _(unknown)_ |
| 34 | `testshortname@test.com` | Test User ShortName | _(none)_ | Sales: Own Documents Only | _(unknown)_ |
| 35 | `testgroupids@test.com` | Test GroupIds User | _(none)_ | Sales: Own Documents Only | _(unknown)_ |
| 36 | `testadmin@test.com` | Test Admin User | Role / Administrator | _(none)_ | _(unknown)_ |

---

## Record Visibility Matrix (after scoping enforcement)

What each user sees when calling `GET /api/v2/search/<model>` (numbers = record count, 403 = ACCESS_DENIED):

| Login | crm | sale | acct | purch | hr | proj | task | activ | cal | prod | partn | /users |
|-------|-----|------|------|-------|----|------|------|-------|-----|------|-------|--------|
| `admin` | 24 | 67 | 38 | 6 | 19 | 13 | 37 | 29 | 0 | 31 | 70 | 20 |
| `user@test.com` | 403 | 403 | 403 | 403 | 403 | 0 | 1 | 1 | 0 | 31 | 70 | 403 |
| `manager@test.com` | 403 | 403 | 403 | 403 | 403 | 0 | 2 | 0 | 0 | 31 | 70 | 1 |
| `sales@test.com` | 8 | 3 | 0 | 403 | 403 | 0 | 1 | 12 | 0 | 31 | 70 | 403 |
| `sales_manager@test.com` | 0 | 2 | 1 | 403 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `crm@test.com` | 0 | 0 | 0 | 403 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `accounting@test.com` | 403 | 0 | 38 | 0 | 403 | 0 | 2 | 0 | 0 | 31 | 70 | 403 |
| `accounting_mgr@test.com` | 403 | 0 | 38 | 0 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `hr@test.com` | 403 | 403 | 403 | 403 | 19 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `hr_manager@test.com` | 403 | 403 | 403 | 403 | 19 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `inventory@test.com` | 403 | 0 | 403 | 0 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `inventory_mgr@test.com` | 403 | 0 | 403 | 0 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `purchase@test.com` | 403 | 403 | 2 | 6 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `purchase_mgr@test.com` | 403 | 403 | 2 | 6 | 403 | 0 | 1 | 0 | 0 | 31 | 70 | 403 |
| `project@test.com` | 403 | 0 | 403 | 403 | 403 | 0 | 13 | 0 | 0 | 31 | 70 | 403 |
| `project_mgr@test.com` | 403 | 0 | 403 | 403 | 403 | 4 | 20 | 0 | 0 | 31 | 70 | 403 |

### Key

- **Number** = total records visible to that user (scoped by ownership/team/department)
- **403** = `ACCESS_DENIED` (user lacks the required module group)
- **0** = user has access but no records match their scope
- `prod` and `partn` are shared resources visible to all internal users
- `/users` requires admin or ERP manager role

---

## Scoping Rules Summary

| Model | Scoping Logic |
|-------|---------------|
| `crm.lead` | Own leads + unassigned + sales team leads |
| `sale.order` | Own orders + unassigned + sales team orders |
| `account.move` | Accounting group: all; others: own invoices |
| `purchase.order` | Purchase group: all; others: own POs |
| `hr.employee` | HR officers/managers: all; others: self + department |
| `hr.contract` | Same as hr.employee (through employee_id) |
| `hr.resume.line` | Same as hr.employee (through employee_id) |
| `project.project` | Favorited + employee-visible + created by user |
| `project.task` | Assigned to user + tasks in employee-visible projects |
| `mail.activity` | Own activities only |
| `calendar.event` | Own events + events user is invited to |
| `product.template` | All (shared resource) |
| `res.partner` | All (shared resource, employees excluded) |
| `/users` | Admin: all; Manager: own dept + created by; Others: 403 |

---

## Login Example

```bash
# Login
curl -s -X POST http://localhost:8069/api/v2/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"sales@test.com","password":"Test@12345"}'

# Use the returned session_token
curl -s http://localhost:8069/api/v2/search/crm.lead \
  -H 'session-token: <token>'
```
