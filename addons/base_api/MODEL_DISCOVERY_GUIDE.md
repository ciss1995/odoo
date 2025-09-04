# 🔍 Odoo Model Discovery Guide

## How I Found `res.users` and How You Can Find Any Model

### Quick Answer: Where Model Names Come From

1. **Odoo Convention**: Model names follow a pattern `module.object` 
   - `res.users` = "Resource Users" (base module)
   - `crm.lead` = "CRM Leads" (crm module)  
   - `hr.employee` = "HR Employees" (hr module)

2. **Common Patterns**:
   - `res.*` = Core resources (users, partners, companies)
   - `crm.*` = Customer Relationship Management
   - `hr.*` = Human Resources
   - `sale.*` = Sales
   - `account.*` = Accounting
   - `product.*` = Products

## 🛠️ **3 Methods to Discover Available Models**

### Method 1: List All Models (Best for Discovery)

```bash
# Get all available models in your Odoo instance
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/ir.model?limit=100"
```

### Method 2: Search by Pattern

```bash
# Find all CRM models
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/ir.model" | grep "crm\."

# Find all HR models  
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/ir.model" | grep "hr\."
```

### Method 3: Test Model Directly

```bash
# Try a model name to see if it exists
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/crm.lead?limit=1"

# If it returns success=true, the model exists!
```

## 📋 **Complete Model Reference by Module**

### **Base/Core Models** (`res.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `res.users` | System users | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.users?limit=5"` |
| `res.partner` | Customers/Suppliers/Contacts | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.partner?limit=5"` |
| `res.company` | Companies | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.company?limit=5"` |
| `res.country` | Countries | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.country?limit=10"` |
| `res.currency` | Currencies | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.currency?limit=10"` |
| `res.config.settings` | System Configuration | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.config.settings?limit=5"` |
| `res.lang` | Languages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.lang?limit=10"` |
| `res.groups` | User Groups | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/res.groups?limit=10"` |

### **CRM Models** (`crm.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `crm.lead` | Leads/Opportunities | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.lead?limit=5"` |
| `crm.stage` | Pipeline Stages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.stage?limit=5"` |
| `crm.team` | Sales Teams | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.team?limit=5"` |
| `crm.team.member` | Team Members | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.team.member?limit=5"` |
| `crm.tag` | CRM Tags | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.tag?limit=10"` |
| `crm.lost.reason` | Lost Reasons | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.lost.reason?limit=10"` |
| `crm.lead.scoring.frequency` | Lead Scoring | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.lead.scoring.frequency?limit=5"` |
| `crm.recurring.plan` | Recurring Plans | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/crm.recurring.plan?limit=5"` |

### **HR Models** (`hr.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `hr.employee` | Employee Records | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.employee?limit=5"` |
| `hr.employee.public` | Public Employee Info | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.employee.public?limit=5"` |
| `hr.department` | Departments | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.department?limit=5"` |
| `hr.job` | Job Positions | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.job?limit=5"` |
| `hr.contract` | Employee Contracts | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.contract?limit=5"` |
| `hr.contract.type` | Contract Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.contract.type?limit=5"` |
| `hr.employee.category` | Employee Categories | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.employee.category?limit=5"` |
| `hr.departure.reason` | Departure Reasons | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.departure.reason?limit=5"` |
| `hr.work.location` | Work Locations | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.work.location?limit=5"` |
| `hr.attendance` | Attendance Records | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.attendance?limit=5"` |
| `hr.leave` | Leave Requests | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.leave?limit=5"` |
| `hr.leave.type` | Leave Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.leave.type?limit=5"` |
| `hr.timesheet` | Timesheets | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.timesheet?limit=5"` |
| `hr.expense` | Expense Records | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.expense?limit=5"` |
| `hr.recruitment.applicant` | Job Applicants | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/hr.recruitment.applicant?limit=5"` |

### **Sales Models** (`sale.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `sale.order` | Sales Orders/Quotations | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/sale.order?limit=5"` |
| `sale.order.line` | Sales Order Lines | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/sale.order.line?limit=5"` |
| `sale.advance.payment.inv` | Advance Payment Invoices | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/sale.advance.payment.inv?limit=5"` |
| `sale.order.discount` | Order Discounts | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/sale.order.discount?limit=5"` |
| `sale.order.cancel` | Order Cancellation | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/sale.order.cancel?limit=5"` |
| `sale.report` | Sales Analysis Report | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/sale.report?limit=5"` |

### **Product Models** (`product.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `product.template` | Product Templates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.template?limit=5"` |
| `product.product` | Product Variants | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.product?limit=5"` |
| `product.category` | Product Categories | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.category?limit=5"` |
| `product.attribute` | Product Attributes | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.attribute?limit=5"` |
| `product.attribute.value` | Attribute Values | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.attribute.value?limit=5"` |
| `product.pricelist` | Pricelists | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.pricelist?limit=5"` |
| `product.pricelist.item` | Pricelist Items | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.pricelist.item?limit=5"` |
| `product.tag` | Product Tags | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.tag?limit=5"` |
| `product.packaging` | Product Packaging | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.packaging?limit=5"` |
| `product.supplierinfo` | Vendor Information | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.supplierinfo?limit=5"` |
| `product.combo` | Product Combos | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.combo?limit=5"` |
| `product.combo.item` | Combo Items | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/product.combo.item?limit=5"` |

### **Accounting Models** (`account.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `account.move` | Journal Entries/Invoices/Bills | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.move?limit=5"` |
| `account.move.line` | Journal Entry Lines | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.move.line?limit=5"` |
| `account.payment` | Payments | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.payment?limit=5"` |
| `account.account` | Chart of Accounts | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.account?limit=10"` |
| `account.journal` | Journals | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.journal?limit=5"` |
| `account.tax` | Taxes | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.tax?limit=5"` |
| `account.analytic.account` | Analytic Accounts | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.analytic.account?limit=5"` |
| `account.bank.statement` | Bank Statements | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.bank.statement?limit=5"` |
| `account.bank.statement.line` | Bank Statement Lines | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.bank.statement.line?limit=5"` |
| `account.reconcile.model` | Reconciliation Models | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.reconcile.model?limit=5"` |
| `account.payment.term` | Payment Terms | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.payment.term?limit=5"` |
| `account.payment.method` | Payment Methods | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.payment.method?limit=5"` |
| `account.cash.rounding` | Cash Rounding | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.cash.rounding?limit=5"` |
| `account.report` | Financial Reports | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/account.report?limit=5"` |

### **Inventory/Stock Models** (`stock.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `stock.picking` | Transfers/Deliveries | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.picking?limit=5"` |
| `stock.move` | Stock Movements | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.move?limit=5"` |
| `stock.move.line` | Stock Move Lines | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.move.line?limit=5"` |
| `stock.quant` | Stock Quantities | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.quant?limit=5"` |
| `stock.location` | Stock Locations | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.location?limit=5"` |
| `stock.warehouse` | Warehouses | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.warehouse?limit=5"` |
| `stock.lot` | Lots/Serial Numbers | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.lot?limit=5"` |
| `stock.picking.type` | Operation Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.picking.type?limit=5"` |
| `stock.orderpoint` | Reordering Rules | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.orderpoint?limit=5"` |
| `stock.scrap` | Scrap Orders | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.scrap?limit=5"` |
| `stock.package.level` | Package Levels | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.package.level?limit=5"` |
| `stock.package.type` | Package Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.package.type?limit=5"` |
| `stock.storage.category` | Storage Categories | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.storage.category?limit=5"` |
| `stock.rule` | Stock Rules | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/stock.rule?limit=5"` |

### **Purchase Models** (`purchase.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `purchase.order` | Purchase Orders/RFQs | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/purchase.order?limit=5"` |
| `purchase.order.line` | Purchase Order Lines | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/purchase.order.line?limit=5"` |
| `purchase.report` | Purchase Analysis | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/purchase.report?limit=5"` |
| `purchase.bill.line.match` | Bill Line Matching | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/purchase.bill.line.match?limit=5"` |

### **Payment Models** (`payment.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `payment.provider` | Payment Providers | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/payment.provider?limit=5"` |
| `payment.method` | Payment Methods | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/payment.method?limit=5"` |
| `payment.transaction` | Payment Transactions | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/payment.transaction?limit=5"` |
| `payment.token` | Payment Tokens | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/payment.token?limit=5"` |

### **Project Management Models** (`project.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `project.project` | Projects | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.project?limit=5"` |
| `project.task` | Tasks | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.task?limit=5"` |
| `project.task.type` | Task Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.task.type?limit=5"` |
| `project.milestone` | Project Milestones | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.milestone?limit=5"` |
| `project.tags` | Project Tags | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.tags?limit=5"` |
| `project.project.stage` | Project Stages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.project.stage?limit=5"` |
| `project.task.recurrence` | Task Recurrence | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.task.recurrence?limit=5"` |
| `project.collaborator` | Project Collaborators | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.collaborator?limit=5"` |
| `project.update` | Project Updates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/project.update?limit=5"` |

### **Survey Models** (`survey.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `survey.survey` | Surveys | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/survey.survey?limit=5"` |
| `survey.question` | Survey Questions | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/survey.question?limit=5"` |
| `survey.user_input` | Survey Responses | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/survey.user_input?limit=5"` |
| `survey.survey.template` | Survey Templates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/survey.survey.template?limit=5"` |

### **Document/Knowledge Models**

| Model | Description | Test Command |
|-------|-------------|--------------|
| `ir.attachment` | File Attachments | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.attachment?limit=5"` |
| `mail.message` | Mail Messages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.message?limit=5"` |
| `mail.thread` | Mail Thread | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.thread?limit=5"` |

### **Website/E-commerce Models** (`website.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `website` | Websites | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website?limit=5"` |
| `website.page` | Website Pages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.page?limit=5"` |
| `website.menu` | Website Menus | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.menu?limit=5"` |

### **UTM/Marketing Models** (`utm.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `utm.campaign` | Marketing Campaigns | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/utm.campaign?limit=5"` |
| `utm.medium` | Marketing Mediums | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/utm.medium?limit=5"` |
| `utm.source` | Marketing Sources | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/utm.source?limit=5"` |

### **Units of Measure Models** (`uom.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `uom.uom` | Units of Measure | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/uom.uom?limit=5"` |
| `uom.category` | UoM Categories | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/uom.category?limit=5"` |

### **Fleet Management Models** (`fleet.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `fleet.vehicle` | Vehicles | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle?limit=5"` |
| `fleet.vehicle.model` | Vehicle Models | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.model?limit=5"` |
| `fleet.vehicle.model.brand` | Vehicle Brands | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.model.brand?limit=5"` |
| `fleet.vehicle.model.category` | Vehicle Categories | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.model.category?limit=5"` |
| `fleet.vehicle.log.contract` | Vehicle Contracts | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.log.contract?limit=5"` |
| `fleet.vehicle.log.services` | Vehicle Services | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.log.services?limit=5"` |
| `fleet.vehicle.assignation.log` | Vehicle Assignments | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.assignation.log?limit=5"` |
| `fleet.vehicle.odometer` | Odometer Readings | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.odometer?limit=5"` |
| `fleet.vehicle.state` | Vehicle States | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.state?limit=5"` |
| `fleet.vehicle.tag` | Vehicle Tags | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.vehicle.tag?limit=5"` |
| `fleet.service.type` | Service Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/fleet.service.type?limit=5"` |

### **Event Management Models** (`event.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `event.event` | Events | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.event?limit=5"` |
| `event.type` | Event Templates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.type?limit=5"` |
| `event.registration` | Event Registrations | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.registration?limit=5"` |
| `event.ticket` | Event Tickets | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.ticket?limit=5"` |
| `event.type.ticket` | Template Tickets | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.type.ticket?limit=5"` |
| `event.stage` | Event Stages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.stage?limit=5"` |
| `event.tag` | Event Tags | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.tag?limit=5"` |
| `event.question` | Event Questions | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.question?limit=5"` |
| `event.question.answer` | Question Answers | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.question.answer?limit=5"` |
| `event.registration.answer` | Registration Answers | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.registration.answer?limit=5"` |
| `event.mail` | Event Mail Templates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/event.mail?limit=5"` |

### **Calendar Models** (`calendar.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `calendar.event` | Calendar Events/Meetings | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/calendar.event?limit=5"` |
| `calendar.attendee` | Event Attendees | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/calendar.attendee?limit=5"` |
| `calendar.alarm` | Event Alarms | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/calendar.alarm?limit=5"` |
| `calendar.recurrence` | Recurring Events | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/calendar.recurrence?limit=5"` |
| `calendar.event.type` | Event Categories | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/calendar.event.type?limit=5"` |
| `calendar.filter` | Calendar Filters | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/calendar.filter?limit=5"` |

### **Website Management Models** (`website.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `website` | Websites | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website?limit=5"` |
| `website.page` | Website Pages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.page?limit=5"` |
| `website.menu` | Website Menus | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.menu?limit=5"` |
| `website.visitor` | Website Visitors | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.visitor?limit=5"` |
| `website.page.properties` | Page Properties | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.page.properties?limit=5"` |
| `website.rewrite` | URL Rewrites | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.rewrite?limit=5"` |
| `website.snippet.filter` | Snippet Filters | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.snippet.filter?limit=5"` |
| `website.configurator.feature` | Configurator Features | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.configurator.feature?limit=5"` |
| `website.form` | Website Forms | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.form?limit=5"` |
| `website.controller.page` | Controller Pages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/website.controller.page?limit=5"` |

### **Manufacturing Models** (`mrp.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `mrp.production` | Manufacturing Orders | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.production?limit=5"` |
| `mrp.bom` | Bills of Materials | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.bom?limit=5"` |
| `mrp.bom.line` | BOM Lines | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.bom.line?limit=5"` |
| `mrp.workorder` | Work Orders | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.workorder?limit=5"` |
| `mrp.workcenter` | Work Centers | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.workcenter?limit=5"` |
| `mrp.routing` | Routings | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.routing?limit=5"` |
| `mrp.routing.workcenter` | Routing Operations | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.routing.workcenter?limit=5"` |
| `mrp.unbuild` | Unbuild Orders | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.unbuild?limit=5"` |
| `mrp.bom.byproduct` | BOM By-products | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mrp.bom.byproduct?limit=5"` |

### **Loyalty & Coupon Models** (`loyalty.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `loyalty.program` | Loyalty Programs | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/loyalty.program?limit=5"` |
| `loyalty.card` | Loyalty Cards/Coupons | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/loyalty.card?limit=5"` |
| `loyalty.rule` | Loyalty Rules | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/loyalty.rule?limit=5"` |
| `loyalty.reward` | Loyalty Rewards | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/loyalty.reward?limit=5"` |
| `loyalty.history` | Loyalty History | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/loyalty.history?limit=5"` |
| `loyalty.mail` | Loyalty Mail Templates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/loyalty.mail?limit=5"` |

### **Mail/Communication Models** (`mail.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `mail.message` | Mail Messages | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.message?limit=5"` |
| `mail.thread` | Mail Thread | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.thread?limit=5"` |
| `mail.template` | Email Templates | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.template?limit=5"` |
| `mail.activity` | Activities | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.activity?limit=5"` |
| `mail.activity.type` | Activity Types | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.activity.type?limit=5"` |
| `mail.followers` | Followers | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.followers?limit=5"` |
| `mail.alias` | Email Aliases | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.alias?limit=5"` |
| `mail.channel` | Chat Channels | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/mail.channel?limit=5"` |

### **System/Technical Models** (`ir.*`)

| Model | Description | Test Command |
|-------|-------------|--------------|
| `ir.model` | Database Models | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.model?limit=5"` |
| `ir.model.fields` | Model Fields | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.model.fields?limit=5"` |
| `ir.attachment` | File Attachments | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.attachment?limit=5"` |
| `ir.ui.view` | Views | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.ui.view?limit=5"` |
| `ir.ui.menu` | Menu Items | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.ui.menu?limit=5"` |
| `ir.actions.act_window` | Window Actions | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.actions.act_window?limit=5"` |
| `ir.cron` | Scheduled Actions | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.cron?limit=5"` |
| `ir.sequence` | Sequences | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.sequence?limit=5"` |
| `ir.rule` | Record Rules | `curl -H "api-key: YOUR_KEY" "http://localhost:8069/api/v2/search/ir.rule?limit=5"` |

## 📋 **Getting More Field Details**

By default, the API only returns basic fields (`id`, `name`, `display_name`). To get more detailed information, you have several options:

### Method 1: Field Discovery (Find Available Fields)

```bash
# First, discover what fields are available for a model
curl -H "api-key: YOUR_KEY" \
     "http://localhost:8069/api/v2/search/ir.model.fields" \
     -G -d "model=crm.lead" -d "limit=50"
```

### Method 2: Request Specific Fields (Requires API Enhancement)

**Note**: The current API implementation is limited to basic fields. For detailed field access, you can:

1. **Use Odoo Web Interface** for detailed field inspection
2. **Use Odoo XML-RPC** for full field access
3. **Enhance the base_api module** (see enhancement section below)

### Method 3: XML-RPC Alternative (Full Field Access)

```python
import xmlrpc.client

# Connect to Odoo
url = 'http://localhost:8069'
db = 'your_database'
username = 'admin'
password = 'admin'

common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
uid = common.authenticate(db, username, password, {})

models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

# Get CRM leads with specific fields
lead_fields = ['name', 'partner_name', 'email_from', 'phone', 'stage_id', 
               'user_id', 'team_id', 'expected_revenue', 'probability', 
               'date_deadline', 'create_date', 'priority']

leads = models.execute_kw(db, uid, password, 'crm.lead', 'search_read',
                         [[]],  # domain
                         {'fields': lead_fields, 'limit': 5})

for lead in leads:
    print(f"Lead: {lead['name']}")
    print(f"Contact: {lead['partner_name']}")
    print(f"Email: {lead['email_from']}")
    print(f"Revenue: {lead['expected_revenue']}")
    print("---")
```

### Method 4: Enhanced API Usage (If Enhanced)

If you enhance the base_api controller to support field specification:

```bash
# Get CRM leads with specific fields
curl -H "api-key: YOUR_KEY" \
     "http://localhost:8069/api/v2/search/crm.lead" \
     -G -d "fields=name,partner_name,email_from,phone,expected_revenue,stage_id,user_id" \
     -d "limit=5"

# Get detailed product information
curl -H "api-key: YOUR_KEY" \
     "http://localhost:8069/api/v2/search/product.template" \
     -G -d "fields=name,list_price,standard_price,categ_id,sale_ok,purchase_ok,type" \
     -d "limit=5"

# Get employee details
curl -H "api-key: YOUR_KEY" \
     "http://localhost:8069/api/v2/search/hr.employee" \
     -G -d "fields=name,work_email,department_id,job_id,manager_id,work_phone" \
     -d "limit=5"
```

### 🔧 **API Enhancement for Field Support**

To enhance the current API to support field specification, modify the `search_model` method in `controllers/simple_api.py`:

```python
@http.route('/api/v2/search/<string:model>', type='http', auth='none', methods=['GET'], csrf=False)
def search_model(self, model):
    """Search any model with authentication and field specification."""
    is_valid, result = self._authenticate()
    if not is_valid:
        return result

    try:
        # Validate model
        if model not in request.env:
            return self._error_response(f"Model '{model}' not found", 404, "MODEL_NOT_FOUND")
        
        model_obj = request.env[model]
        
        # Get parameters
        limit = int(request.httprequest.args.get('limit', 10))
        offset = int(request.httprequest.args.get('offset', 0))
        fields_param = request.httprequest.args.get('fields', '')
        
        # Handle field specification
        if fields_param:
            requested_fields = [f.strip() for f in fields_param.split(',')]
            # Add 'id' if not present (always needed)
            if 'id' not in requested_fields:
                requested_fields.insert(0, 'id')
            # Validate fields exist in model
            available_fields = [f for f in requested_fields if f in model_obj._fields]
        else:
            # Default basic fields
            basic_fields = ['id', 'name', 'display_name']
            available_fields = [f for f in basic_fields if f in model_obj._fields]
        
        # Basic domain
        domain = []
        if 'active' in model_obj._fields:
            domain.append(('active', '=', True))
        
        # Search records
        records = model_obj.search(domain, limit=limit, offset=offset, order='id')
        
        # Read specified fields
        records_data = records.read(available_fields)
        
        return self._json_response(
            data={
                'records': records_data,
                'count': len(records_data),
                'model': model,
                'fields': available_fields
            },
            message=f"Found {len(records_data)} records in {model}"
        )
```

### 📊 **Common Field Sets by Model**

#### CRM Lead Fields
```
Basic: name, partner_name, email_from, phone
Commercial: expected_revenue, probability, stage_id, user_id, team_id
Dates: date_deadline, create_date, date_open, date_closed
Details: description, priority, type, source_id, medium_id, campaign_id
```

#### Product Fields  
```
Basic: name, default_code, list_price, standard_price
Classification: categ_id, product_brand_id, product_tag_ids
Behavior: sale_ok, purchase_ok, type, tracking
Inventory: qty_available, virtual_available, route_ids
```

#### Employee Fields
```
Personal: name, work_email, work_phone, mobile_phone
Organization: department_id, job_id, manager_id, company_id
Contract: employee_type, resource_calendar_id, tz
Status: active, user_id, employee_bank_account_id
```

#### Sale Order Fields
```
Header: name, partner_id, date_order, state, amount_total
Commercial: user_id, team_id, pricelist_id, payment_term_id
Delivery: commitment_date, expected_date, carrier_id
Invoice: invoice_status, invoice_ids, payment_term_id
```

## 🧪 **Practical Examples**

### Example 1: Working with CRM

```bash
# List all CRM leads
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/crm.lead?limit=10"

# Create a new CRM lead
curl -X POST \
     -H "api-key: YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "API Generated Lead",
       "partner_name": "Potential Customer",
       "email_from": "customer@example.com",
       "phone": "+1-555-0123",
       "expected_revenue": 5000.00
     }' \
     "http://localhost:8069/api/v2/create/crm.lead"

# List CRM stages
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/crm.stage?limit=10"
```

### Example 2: Working with HR

```bash
# List all employees
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/hr.employee?limit=10"

# Create a new employee
curl -X POST \
     -H "api-key: YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "John Smith",
       "work_email": "john.smith@company.com",
       "department_id": 1,
       "job_id": 1
     }' \
     "http://localhost:8069/api/v2/create/hr.employee"

# List departments
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/hr.department?limit=10"

# List job positions
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/hr.job?limit=10"
```

### Example 3: Working with Sales

```bash
# List sales orders
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/sale.order?limit=10"

# List product categories
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/product.category?limit=10"

# Create a new product
curl -X POST \
     -H "api-key: YOUR_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "API Product",
       "list_price": 99.99,
       "sale_ok": true,
       "purchase_ok": true
     }' \
     "http://localhost:8069/api/v2/create/product.template"
```

## 🔍 **Model Discovery Script**

Here's a Python script to discover all available models:

```python
import requests
import json

API_KEY = "YOUR_API_KEY"
BASE_URL = "http://localhost:8069/api/v2"

def discover_models():
    headers = {"api-key": API_KEY}
    
    # Get all models
    response = requests.get(f"{BASE_URL}/search/ir.model?limit=100", headers=headers)
    
    if response.status_code == 200:
        data = response.json()
        if data['success']:
            models = []
            for record in data['data']['records']:
                # Try to extract model name from the record
                model_name = record.get('model', '')
                if model_name:
                    models.append(model_name)
            
            # Group by module
            modules = {}
            for model in models:
                module = model.split('.')[0] if '.' in model else 'other'
                if module not in modules:
                    modules[module] = []
                modules[module].append(model)
            
            # Print organized results
            for module, model_list in sorted(modules.items()):
                print(f"\\n{module.upper()} MODULE:")
                for model in sorted(model_list):
                    print(f"  - {model}")
    else:
        print("Failed to fetch models")

if __name__ == "__main__":
    discover_models()
```

## 🎯 **Quick Reference for Your Use Case**

**Based on what you asked about:**

1. **Users**: `res.users`
2. **CRM**: `crm.lead`, `crm.stage`, `crm.team`
3. **HR**: `hr.employee`, `hr.department`, `hr.job`

**Your working API key**: `YOUR_API_KEY`

**Test these right now:**

```bash
# CRM
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/crm.lead?limit=5"

# HR  
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/hr.employee?limit=5"

# Users
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/search/res.users?limit=5"
```

**The pattern is always**: `/api/v2/search/{model_name}?limit=X`

## **🔐 User Authentication & Access Control**

### **Method 1: User Login Authentication (NEW!)**

```bash
# Login with username/password
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -H "Content-Type: application/json" \
     -d '{
       "username": "demo_user",
       "password": "demo_password"
     }'

# Response includes session token:
{
  "success": true,
  "data": {
    "session_token": "ABC123...",
    "expires_at": "2024-01-16T10:30:00",
    "user": {
      "id": 5,
      "name": "Demo User",
      "login": "demo_user",
      "groups": ["Employee", "Sales User"]
    }
  }
}

# Use session token for API calls
curl -H "session-token: ABC123..." \
     "http://localhost:8069/api/v2/search/crm.lead?limit=5"

# Get current user info
curl -H "session-token: ABC123..." \
     "http://localhost:8069/api/v2/auth/me"

# Logout (invalidate session)
curl -X POST -H "session-token: ABC123..." \
     "http://localhost:8069/api/v2/auth/logout"
```

### **🛡️ Role-Based Access Control**

When users are created with limited permissions, the API automatically enforces access control:

```bash
# Limited user can only access what they're allowed to
curl -H "session-token: LIMITED_USER_TOKEN" \
     "http://localhost:8069/api/v2/search/crm.lead?limit=5"
# ✅ Succeeds if user has CRM access

curl -H "session-token: LIMITED_USER_TOKEN" \
     "http://localhost:8069/api/v2/search/account.move?limit=5"  
# ❌ Fails with "Access denied" if user lacks accounting access
```

### **🔧 Access Control Examples**

#### **Sales User (Limited Access)**
```bash
# Sales users can access:
✅ crm.lead, crm.stage, crm.team
✅ sale.order, sale.order.line  
✅ product.template, product.product
✅ res.partner (customers)

# But cannot access:
❌ account.move (invoices)
❌ hr.employee (HR data) 
❌ res.users (user management)
```

#### **Accounting User (Limited Access)**
```bash  
# Accounting users can access:
✅ account.move, account.move.line
✅ account.payment, account.journal
✅ res.partner (suppliers/customers)
✅ product.template (for invoicing)

# But cannot access:
❌ hr.employee (HR data)
❌ crm.lead (sales data)
❌ res.users (user management)
```

## **👥 User Groups & Permissions**

### **🔍 Discovering Available Groups**

```bash
# Get all available groups (requires admin privileges)
curl -H "api-key: YOUR_ADMIN_KEY" \
     "http://localhost:8069/api/v2/groups"

# Response shows groups organized by category:
{
  "success": true,
  "data": {
    "groups_by_category": {
      "Administration": [
        {"id": 1, "name": "Settings", "full_name": "Administration / Settings"},
        {"id": 2, "name": "Access Rights", "full_name": "Administration / Access Rights"}
      ],
      "Sales": [
        {"id": 15, "name": "User: Own Documents Only", "full_name": "Sales / User: Own Documents Only"},
        {"id": 16, "name": "User: All Documents", "full_name": "Sales / User: All Documents"},
        {"id": 17, "name": "Administrator", "full_name": "Sales / Administrator"}
      ],
      "Human Resources": [
        {"id": 25, "name": "Officer", "full_name": "Human Resources / Officer"},
        {"id": 26, "name": "Manager", "full_name": "Human Resources / Manager"}
      ]
    }
  }
}
```

### **🎯 Common Group Categories & Their Purposes**

#### **👑 Administration**
- **`Settings`** (`base.group_system`) - Full system access
- **`Access Rights`** (`base.group_erp_manager`) - User management

#### **💼 Sales**
- **`User: Own Documents Only`** (`sales_team.group_sale_salesman`) - Can only see own sales
- **`User: All Documents`** (`sales_team.group_sale_salesman_all_leads`) - Can see all sales
- **`Administrator`** (`sales_team.group_sale_manager`) - Full sales management

#### **🏢 CRM**
- **`User`** (`base.group_user`) - Basic CRM access
- **`Use Leads`** (`crm.group_use_lead`) - Can work with leads

#### **👥 Human Resources**
- **`Officer`** (`hr.group_hr_user`) - Basic HR access
- **`Manager`** (`hr.group_hr_manager`) - Full HR management

#### **💰 Accounting**
- **`Billing`** (`account.group_account_invoice`) - Can create/manage invoices
- **`Billing Manager`** (`account.group_account_manager`) - Full accounting access
- **`Show Full Accounting Features`** (`account.group_account_user`) - Full accounting features

#### **📦 Inventory**
- **`User`** (`stock.group_stock_user`) - Basic inventory access
- **`Manager`** (`stock.group_stock_manager`) - Full inventory management

### **👤 Creating Users with Groups**

#### **Method 1: Auto-Generate Credentials (Recommended)**
```bash
# Creates user with auto-generated password and API key
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Sales Representative",
       "login": "sales_rep",
       "email": "sales@company.com",
       "group_names": ["User: Own Documents Only", "Internal User"]
     }'

# Response includes ready-to-use credentials:
{
  "success": true,
  "data": {
    "id": 15,
    "name": "Sales Representative", 
    "login": "sales_rep",
    "email": "sales@company.com",
    "groups": [{"id": 10, "name": "User: Own Documents Only"}],
    "credentials": {
      "temporary_password": "TmpPass123",
      "api_key": "abc123def456...",
      "note": "Store these credentials securely - they won't be shown again"
    }
  }
}
```

#### **Method 2: Manual Password (Skip Auto-Generation)**
```bash
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "HR Manager", 
       "login": "hr_manager",
       "email": "hr@company.com",
       "password": "MySecurePassword123",
       "auto_generate_credentials": false,
       "group_names": ["Manager", "Internal User"]
     }'
```

#### **Method 3: Using Group IDs**
```bash
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Accounting User",
       "login": "accounting_user",
       "email": "accounting@company.com",
       "group_ids": [15, 25, 26]
     }'
```

### **🔑 API Key Management**

#### **Generate API Key for User**
```bash
# Admin generates API key for any user
curl -X POST "http://localhost:8069/api/v2/users/15/api-key" \
     -H "api-key: ADMIN_API_KEY"

# User generates their own API key  
curl -X POST "http://localhost:8069/api/v2/users/15/api-key" \
     -H "session-token: USER_SESSION_TOKEN"

# Response:
{
  "success": true,
  "data": {
    "user_id": 15,
    "user_name": "Sales Representative",
    "api_key": "abc123def456...",
    "note": "Store this API key securely - it will not be shown again"
  }
}
```

#### **Method 4: Creating Different User Types with Auto-Credentials**

**Sales User (Limited Access):**
```bash
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "John Sales",
       "login": "john_sales",
       "email": "john@company.com", 
       "group_names": ["User: Own Documents Only", "Internal User"]
     }'
# Returns: temporary password + API key
```

**Accounting User:**
```bash
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Jane Accountant",
       "login": "jane_accounting",
       "email": "jane@company.com",
       "group_names": ["Billing", "Internal User"]
     }'
# Returns: temporary password + API key
```

**HR Manager:**
```bash
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: YOUR_ADMIN_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Mike HR Manager", 
       "login": "mike_hr",
       "email": "mike@company.com",
       "group_names": ["Manager", "Internal User"]
     }'
# Returns: temporary password + API key
```

### **🔒 Access Control Examples**

Once users are created with specific groups, they automatically get the right permissions:

```bash
# Sales user can access their CRM data
curl -H "session-token: SALES_USER_TOKEN" \
     "http://localhost:8069/api/v2/search/crm.lead?limit=5"
# ✅ Works - they have sales access

# But cannot access HR data  
curl -H "session-token: SALES_USER_TOKEN" \
     "http://localhost:8069/api/v2/search/hr.employee?limit=5"
# ❌ Fails - "Access denied for model 'hr.employee'"

# HR Manager can access employee data
curl -H "session-token: HR_MANAGER_TOKEN" \
     "http://localhost:8069/api/v2/search/hr.employee?limit=5"
# ✅ Works - they have HR management access

# Accounting user can access invoices
curl -H "session-token: ACCOUNTING_USER_TOKEN" \
     "http://localhost:8069/api/v2/search/account.move?limit=5"
# ✅ Works - they have billing access
```

## **🔧 User Management APIs**

### **🔐 Password Management**

#### **Change Own Password**
```bash
# User changes their own password (requires old password)
curl -X PUT "http://localhost:8069/api/v2/users/5/password" \
     -H "session-token: USER_SESSION_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "old_password": "current_password",
       "new_password": "new_secure_password"
     }'
```

#### **Admin Changes User Password**
```bash
# Admin can change any user's password (no old password required)
curl -X PUT "http://localhost:8069/api/v2/users/5/password" \
     -H "api-key: ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "new_password": "new_secure_password"
     }'
```

#### **Admin Resets Password (Generates Temporary)**
```bash
# Admin generates a temporary password for user
curl -X POST "http://localhost:8069/api/v2/users/5/reset-password" \
     -H "api-key: ADMIN_API_KEY"

# Response includes temporary password:
{
  "success": true,
  "data": {
    "user_id": 5,
    "temporary_password": "TmpPass123",
    "message": "Password has been reset. User should change it on first login."
  }
}
```

### **👤 Profile Management**

#### **Update Own Profile**
```bash
# Users can update their own profile information
curl -X PUT "http://localhost:8069/api/v2/users/5" \
     -H "session-token: USER_SESSION_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "John Updated Name",
       "email": "john.new@company.com",
       "phone": "+1-555-1234",
       "mobile": "+1-555-5678",
       "lang": "en_US",
       "tz": "America/New_York"
     }'
```

#### **Admin Updates User**
```bash
# Admins can update any user's information including groups
curl -X PUT "http://localhost:8069/api/v2/users/5" \
     -H "api-key: ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Updated User Name",
       "email": "updated@company.com",
       "active": true,
       "group_names": ["User: All Documents", "Internal User"]
     }'
```

### **📋 User Information**

#### **Get Own Profile**
```bash
# Get current user's profile information
curl -H "session-token: USER_SESSION_TOKEN" \
     "http://localhost:8069/api/v2/users/5"
```

#### **Get Any User (Admin)**
```bash
# Admin can get any user's detailed information
curl -H "api-key: ADMIN_API_KEY" \
     "http://localhost:8069/api/v2/users/5"

# Response includes full user data:
{
  "success": true,
  "data": {
    "user": {
      "id": 5,
      "name": "John Doe",
      "login": "john_doe",
      "email": "john@company.com",
      "phone": "+1-555-1234",
      "active": true,
      "groups": [
        {"id": 15, "name": "User: Own Documents Only", "full_name": "Sales / User: Own Documents Only"},
        {"id": 1, "name": "Internal User", "full_name": "User types / Internal User"}
      ],
      "company_id": [1, "My Company"],
      "create_date": "2024-01-15T10:30:00",
      "login_date": "2024-01-16T09:15:00"
    }
  }
}
```

#### **List All Users**
```bash
# List users with pagination and search
curl -H "api-key: YOUR_API_KEY" \
     "http://localhost:8069/api/v2/users?limit=20&offset=0&search=john&active_only=true"

# Response shows user list:
{
  "success": true,
  "data": {
    "users": [
      {
        "id": 5,
        "name": "John Doe",
        "login": "john_doe",
        "email": "john@company.com",
        "active": true,
        "groups": ["User: Own Documents Only", "Internal User"]
      }
    ],
    "count": 1,
    "total_count": 15,
    "limit": 20,
    "offset": 0
  }
}
```

### **🛡️ Permission-Based Access**

#### **User-Editable Fields**
Regular users can update these fields on their own profile:
- `name` - Full name
- `email` - Email address  
- `phone` - Phone number
- `mobile` - Mobile number
- `signature` - Email signature
- `lang` - Language preference
- `tz` - Timezone

#### **Admin-Only Fields**
Only admins can update these fields:
- `login` - Username
- `active` - Account status
- `groups_id` / `group_names` - Group memberships
- `company_id` - Primary company
- `company_ids` - Accessible companies

### **🔄 Complete User Lifecycle Examples**

#### **1. Streamlined User Creation (NEW - Recommended)**
```bash
# Step 1: Admin creates user with auto-generated credentials
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "New Employee",
       "login": "new_employee",
       "email": "new@company.com",
       "group_names": ["User: Own Documents Only", "Internal User"]
     }'

# Response includes everything needed:
{
  "success": true,
  "data": {
    "id": 25,
    "name": "New Employee",
    "login": "new_employee", 
    "email": "new@company.com",
    "credentials": {
      "temporary_password": "TmpPass123",
      "api_key": "xyz789abc456...",
      "note": "Store these credentials securely - they won't be shown again"
    }
  }
}

# Step 2: User can immediately use either credential method
# Option A: Login with password to get session token
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -d '{"username": "new_employee", "password": "TmpPass123"}'

# Option B: Use API key directly
curl -H "api-key: xyz789abc456..." \
     "http://localhost:8069/api/v2/search/crm.lead?limit=5"

# Step 3: User changes password (optional but recommended)
curl -X PUT "http://localhost:8069/api/v2/users/25/password" \
     -H "session-token: USER_SESSION_TOKEN" \
     -d '{
       "old_password": "TmpPass123",
       "new_password": "my_secure_password"
     }'

# Step 4: User updates profile
curl -X PUT "http://localhost:8069/api/v2/users/25" \
     -H "session-token: USER_SESSION_TOKEN" \
     -d '{
       "phone": "+1-555-9999",
       "lang": "en_US",
       "tz": "America/New_York"
     }'
```

#### **2. Traditional User Creation (Legacy)**
```bash
# Step 1: Admin creates user
curl -X POST "http://localhost:8069/api/v2/create/res.users" \
     -H "api-key: ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "New Employee",
       "login": "new_employee",
       "email": "new@company.com",
       "password": "welcome123",
       "auto_generate_credentials": false,
       "group_names": ["User: Own Documents Only", "Internal User"]
     }'

# Step 2: Generate API key separately (if needed)
curl -X POST "http://localhost:8069/api/v2/users/NEW_USER_ID/api-key" \
     -H "api-key: ADMIN_API_KEY"

# Step 3: User logs in
curl -X POST "http://localhost:8069/api/v2/auth/login" \
     -d '{"username": "new_employee", "password": "welcome123"}'

# Step 4: User changes password
curl -X PUT "http://localhost:8069/api/v2/users/NEW_USER_ID/password" \
     -H "session-token: USER_SESSION_TOKEN" \
     -d '{
       "old_password": "welcome123",
       "new_password": "my_secure_password"
     }'
```

#### **2. Admin User Management Workflow**
```bash
# List all users
curl -H "api-key: ADMIN_API_KEY" \
     "http://localhost:8069/api/v2/users?limit=50"

# Update user groups
curl -X PUT "http://localhost:8069/api/v2/users/USER_ID" \
     -H "api-key: ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
       "group_names": ["Administrator", "Internal User"]
     }'

# Reset forgotten password
curl -X POST "http://localhost:8069/api/v2/users/USER_ID/reset-password" \
     -H "api-key: ADMIN_API_KEY"

# Deactivate user
curl -X PUT "http://localhost:8069/api/v2/users/USER_ID" \
     -H "api-key: ADMIN_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{"active": false}'
```

You can use ANY model that exists in your Odoo instance with proper permissions! 🚀
