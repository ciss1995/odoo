# Notifications & Messaging API Guide

## Overview

Odoo has a full notification system accessible via the `base_api` REST endpoints. It is built on several interconnected models:

| Model | Purpose | Key Use |
|-------|---------|---------|
| `mail.message` | All messages, notes, and notifications attached to records | Inbox, chatter, log notes |
| `mail.notification` | Per-recipient delivery tracking for each message | Read/unread status, email delivery status |
| `mail.activity` | Scheduled tasks/reminders assigned to users on records | To-dos, calls, meetings, follow-ups |
| `mail.activity.type` | Definitions for activity types | Email, Call, Meeting, To-Do, Document |
| `mail.followers` | Subscription records (who follows which document) | Auto-notification on document changes |
| `mail.message.subtype` | Categories of notifications (Discussions, Note, Stage Changed, etc.) | Controls what followers receive |

All of these are accessible through the generic search/create/update/delete endpoints.

---

## Notification Models & Fields

### `mail.notification` (13 fields)

Per-recipient notification record. Tracks whether a specific user received and read a message.

| Field | Type | Description |
|-------|------|-------------|
| `mail_message_id` | many2one | The message this notification belongs to |
| `res_partner_id` | many2one | The recipient partner |
| `author_id` | many2one | The message author |
| `notification_type` | selection | `inbox` or `email` |
| `notification_status` | selection | `ready`, `sent`, `bounce`, `exception`, `canceled` |
| `is_read` | boolean | Whether the recipient has read it |
| `read_date` | datetime | When it was read |
| `failure_type` | selection | `mail_smtp`, `mail_email_invalid`, etc. |
| `failure_reason` | text | Detailed failure description |
| `mail_mail_id` | many2one | The outgoing mail record |
| `mail_email_address` | char | Recipient email address |

### `mail.message` (58 fields)

The core message/notification record. Every chatter post, email, log note, and system notification is a `mail.message`.

| Field | Type | Description |
|-------|------|-------------|
| `subject` | char | Message subject |
| `body` | html | Message content (HTML) |
| `preview` | char | Plain text preview |
| `author_id` | many2one | Author (partner) |
| `date` | datetime | Message date |
| `message_type` | selection | `email`, `comment`, `notification`, `user_notification`, `auto_comment` |
| `model` | char | Related document model (e.g. `res.partner`, `sale.order`) |
| `res_id` | many2one_reference | Related document ID |
| `record_name` | char | Display name of the related record |
| `subtype_id` | many2one | Message subtype (Discussions, Note, Stage Changed, etc.) |
| `is_internal` | boolean | Internal/employee-only message |
| `starred` | boolean | Whether current user starred it |
| `needaction` | boolean | Whether current user has a pending action (inbox item) |
| `notification_ids` | one2many | Individual notification records |
| `partner_ids` | many2many | Explicit recipients |
| `notified_partner_ids` | many2many | Partners who were notified |
| `starred_partner_ids` | many2many | Partners who starred this message |
| `attachment_ids` | many2many | File attachments |
| `tracking_value_ids` | one2many | Field change tracking values |
| `parent_id` | many2one | Parent message (for threading) |
| `child_ids` | one2many | Reply messages |
| `email_from` | char | Sender email |
| `has_error` | boolean | Whether sending failed |

### `mail.activity` (32 fields)

Scheduled action/task assigned to a user on a specific record.

| Field | Type | Description |
|-------|------|-------------|
| `summary` | char | Activity title |
| `note` | html | Detailed description |
| `date_deadline` | date | Due date |
| `date_done` | date | Completion date |
| `state` | selection | `overdue`, `today`, `planned` (computed field — may not work reliably as a search filter; prefer filtering by `date_deadline` instead) |
| `user_id` | many2one | Assigned user |
| `activity_type_id` | many2one | Type (Email, Call, Meeting, To-Do, Document) |
| `res_model` | char | Related model name |
| `res_model_id` | many2one | Related model (ir.model) |
| `res_id` | many2one_reference | Related record ID |
| `res_name` | char | Related record display name |
| `feedback` | text | Completion feedback |
| `automated` | boolean | Whether it was auto-created |
| `can_write` | boolean | Whether current user can edit it |
| `icon` | char | FontAwesome icon (e.g. `fa-phone`) |
| `attachment_ids` | many2many | Attached files |
| `calendar_event_id` | many2one | Linked calendar event (for meetings) |

### `mail.activity.type` (27 fields)

Definitions for the available activity types.

| Field | Type | Description |
|-------|------|-------------|
| `name` | char | Type name (Email, Call, Meeting, etc.) |
| `summary` | char | Default summary text |
| `icon` | char | FontAwesome icon |
| `category` | selection | `default`, `phonecall`, `meeting`, `upload_file` |
| `delay_count` | integer | Default scheduling delay |
| `delay_unit` | selection | `days`, `weeks`, `months` |
| `delay_from` | selection | `current_date`, `previous_activity` |
| `res_model` | char | Model restriction (empty = available for all) |
| `chaining_type` | selection | `suggest` or `trigger` next activity |
| `default_user_id` | many2one | Default assignee |

### `mail.followers` (9 fields)

Tracks who follows (is subscribed to) which document.

| Field | Type | Description |
|-------|------|-------------|
| `partner_id` | many2one | The follower (partner) |
| `res_model` | char | Document model |
| `res_id` | many2one_reference | Document record ID |
| `name` | char | Follower name |
| `email` | char | Follower email |
| `subtype_ids` | many2many | Which notification subtypes they receive |
| `is_active` | boolean | Whether the follower is active |

### `mail.message.subtype` (16 fields)

Defines categories/types of notifications that followers can subscribe to.

| Field | Type | Description |
|-------|------|-------------|
| `name` | char | Subtype name |
| `description` | text | Description |
| `internal` | boolean | Internal only (employees) |
| `hidden` | boolean | Hidden from UI |
| `default` | boolean | Subscribed by default |
| `res_model` | char | Model-specific (empty = global) |
| `sequence` | integer | Display order |

---

## Available Activity Types

These are the built-in activity types available in the system:

| ID | Name | Icon | Category | Default Delay |
|----|------|------|----------|---------------|
| 1 | Email | `fa-envelope` | default | 0 days |
| 2 | Call | `fa-phone` | phonecall | 2 days |
| 3 | Meeting | `fa-users` | meeting | 0 days |
| 4 | To-Do | `fa-check` | default | 5 days |
| 5 | Document | `fa-upload` | upload_file | 5 days |
| 7 | Certifications | `fa-upload` | upload_file | 5 days (HR only) |

---

## Available Message Subtypes

| ID | Name | Internal | Model | Description |
|----|------|----------|-------|-------------|
| 1 | Discussions | No | (global) | General discussions |
| 2 | Note | Yes | (global) | Internal notes |
| 3 | Activities | Yes | (global) | Activity notifications |
| 4 | Invitation | No | calendar.event | Calendar invitations |
| 5 | Validated | No | account.move | Invoice validated |
| 6 | Paid | No | account.move | Invoice paid |
| 8 | Opportunity Created | No | crm.lead | Lead/Opportunity created |
| 9 | Stage Changed | No | crm.lead | Stage changed |
| 10 | Opportunity Won | No | crm.lead | Opportunity won |
| 11 | Opportunity Lost | No | crm.lead | Opportunity lost |
| 23 | Sale Order Confirmed | No | sale.order | Sale order confirmed |

---

## Permissions by Role

### `mail.notification` - Read/Write Access

| Action | Admin | Internal User | Sales User |
|--------|-------|---------------|------------|
| Read notifications | Yes (all) | Yes (all) | Yes (all) |
| Update (mark read) | Yes | Yes | Yes |

### `mail.message` - Read/Write Access

| Action | Admin | Internal User | Sales User |
|--------|-------|---------------|------------|
| Read messages | Yes (all, 257 total) | Yes (filtered by access rules, 89 visible) | Yes (filtered, 89 visible) |
| Create messages | Yes | Depends on target model access | Yes (on models they can access) |
| Post on `res.partner` | Yes | Requires `base.group_partner_manager` | Yes |
| Post on `crm.lead` | Yes | Requires CRM access | Yes |
| Post on `sale.order` | Yes | Requires Sales access | Yes |

### `mail.activity` - Read/Write Access

| Action | Admin | Internal User | Sales User |
|--------|-------|---------------|------------|
| View all activities | Yes | Yes (filtered by model access) | Yes (filtered by model access) |
| View own activities | Yes | Yes | Yes |
| Create activity | Yes | Depends on target model | Yes (on accessible models) |
| Update activity | Yes | Only own activities | Only own activities |
| Delete activity | Yes | Yes (own activities) | Yes (own activities) |

### `mail.followers` - Read/Write Access

| Action | Admin | Internal User | Sales User |
|--------|-------|---------------|------------|
| View followers | Yes (all, 11 records) | Yes (all, 11 records) | Yes (all, 11 records) |

### `mail.activity.type` - Read Access

| Action | Admin | Internal User | Sales User |
|--------|-------|---------------|------------|
| View activity types | Yes (all 6 types) | Yes (all 6 types) | Yes (all 6 types) |

### `mail.message.subtype` - Read Access

| Action | Admin | Internal User | Sales User |
|--------|-------|---------------|------------|
| View subtypes | Yes (all 31 subtypes) | Yes (all 31 subtypes) | Yes (all 31 subtypes) |

---

## API Endpoints & Responses

### 1. Read Notifications

#### `GET /api/v2/search/mail.notification` - List Notifications

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.notification?limit=10&fields=mail_message_id,res_partner_id,notification_type,notification_status,is_read,read_date,failure_type,failure_reason,author_id"
```

**Response (all roles):**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 1,
        "mail_message_id": [332, "My Company Quotation (Ref S00008)"],
        "res_partner_id": [52, "Leo Garcia"],
        "notification_type": "email",
        "notification_status": "exception",
        "is_read": true,
        "read_date": "2026-03-21 22:50:14",
        "failure_type": "mail_smtp",
        "failure_reason": false,
        "author_id": false
      }
    ],
    "count": 1,
    "model": "mail.notification",
    "total_count": 1
  },
  "message": "Found 1 records in mail.notification"
}
```

#### Filter by read status

```bash
# Unread notifications
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.notification?is_read=false&fields=mail_message_id,res_partner_id,notification_status"

# Failed notifications
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.notification?notification_status=exception&fields=mail_message_id,res_partner_id,failure_type,failure_reason"
```

---

### 2. Messages (Inbox / Chatter / Notes)

#### `GET /api/v2/search/mail.message` - List Messages

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?limit=5&fields=subject,body,author_id,date,message_type,model,res_id,record_name,starred,needaction,is_internal"
```

**Admin Response (257 total messages visible):**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 1,
        "subject": "Welcome to Odoo!",
        "body": "<p>Welcome to the #general channel.</p>\n            <p>This channel is accessible to all users to <b>easily share company information</b>.</p>",
        "author_id": [2, "OdooBot"],
        "date": "2026-03-21 05:08:40",
        "message_type": "email",
        "model": "discuss.channel",
        "res_id": 1,
        "record_name": "general",
        "starred": false,
        "needaction": false,
        "is_internal": false
      },
      {
        "id": 2,
        "subject": false,
        "body": "<p>Server Action created</p>",
        "author_id": [2, "OdooBot"],
        "date": "2026-03-21 05:08:40",
        "message_type": "notification",
        "model": "ir.actions.server",
        "res_id": 133,
        "record_name": "Mail: Email Queue Manager",
        "starred": false,
        "needaction": false,
        "is_internal": true
      }
    ],
    "count": 5,
    "model": "mail.message",
    "total_count": 257
  }
}
```

**Regular / Sales User Response (89 total messages visible - filtered by access rules):**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 1,
        "subject": "Welcome to Odoo!",
        "body": "<p>Welcome to the #general channel.</p>\n            <p>This channel is accessible to all users to <b>easily share company information</b>.</p>",
        "author_id": [2, "OdooBot"],
        "date": "2026-03-21 05:08:40",
        "message_type": "email",
        "model": "discuss.channel",
        "res_id": 1,
        "record_name": "general",
        "starred": false,
        "needaction": false
      }
    ],
    "count": 1,
    "model": "mail.message",
    "total_count": 89
  }
}
```

#### Filter: Inbox (need action)

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?needaction=true&fields=subject,body,author_id,date,message_type,model,res_id,record_name"
```

#### Filter: Starred messages

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?starred=true&fields=subject,body,author_id,date,message_type,model,res_id,record_name"
```

#### Filter: Messages for a specific record

```bash
# All messages on partner ID 9
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?model=res.partner&res_id=9&fields=subject,body,author_id,date,message_type,record_name,starred,needaction"
```

**Response:**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 99,
        "subject": false,
        "body": "<p>Contact created</p>",
        "author_id": [3, "Administrator"],
        "date": "2026-03-21 05:12:56",
        "message_type": "notification",
        "record_name": "Test Customer",
        "starred": false,
        "needaction": false
      },
      {
        "id": 341,
        "subject": false,
        "body": "<p>Admin note: Customer needs VIP treatment</p>",
        "author_id": [3, "Administrator"],
        "date": "2026-03-22 01:00:51",
        "message_type": "comment",
        "record_name": "Test Customer",
        "starred": false,
        "needaction": false
      },
      {
        "id": 342,
        "subject": false,
        "body": "<p>Regular user note on partner</p>",
        "author_id": [7, "Regular User"],
        "date": "2026-03-22 01:00:51",
        "message_type": "comment",
        "record_name": "Test Customer",
        "starred": false,
        "needaction": false
      },
      {
        "id": 343,
        "subject": false,
        "body": "<p>Sales update: Customer interested in new product</p>",
        "author_id": [8, "Sales User"],
        "date": "2026-03-22 01:00:51",
        "message_type": "comment",
        "record_name": "Test Customer",
        "starred": false,
        "needaction": false
      }
    ],
    "count": 4,
    "total_count": 4
  }
}
```

#### Filter: By message type

```bash
# Comments only (user-posted messages)
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?message_type=comment&limit=5&fields=subject,body,author_id,date,model,res_id,record_name"

# System notifications only
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?message_type=notification&limit=5&fields=subject,body,author_id,date,model,res_id,record_name"
```

#### Get specific message by ID

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message/341?fields=subject,body,author_id,date,message_type,model,res_id,record_name,starred,needaction,notification_ids,partner_ids"
```

**Response:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 341,
      "subject": false,
      "body": "<p>Admin note: Customer needs VIP treatment</p>",
      "author_id": [3, "Administrator"],
      "date": "2026-03-22 01:00:51",
      "message_type": "comment",
      "model": "res.partner",
      "res_id": 9,
      "record_name": "Test Customer",
      "starred": false,
      "needaction": false,
      "notification_ids": [],
      "partner_ids": []
    },
    "model": "mail.message",
    "id": 341,
    "total_fields_available": 58
  },
  "message": "Found record 341 in mail.message"
}
```

---

### 3. Post a Message / Note on a Record

#### `POST /api/v2/create/mail.message` - Create Message

```bash
curl -X POST -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/create/mail.message" \
  -d '{
    "body": "<p>Important update about this customer</p>",
    "message_type": "comment",
    "model": "res.partner",
    "res_id": 9,
    "subtype_id": 1
  }'
```

**Subtype IDs for posting:**
- `1` = Discussions (visible to followers, external)
- `2` = Note (internal only, employee-visible)

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "id": 341,
    "record": {
      "id": 341,
      "display_name": false,
      "subject": false,
      "date": "2026-03-22 01:00:51",
      "body": "<p>Admin note: Customer needs VIP treatment</p>",
      "preview": "Admin note: Customer needs VIP treatment",
      "model": "res.partner",
      "res_id": 9,
      "record_name": "Test Customer",
      "message_type": "comment",
      "subtype_id": [1, "Discussions"],
      "is_internal": false,
      "author_id": [3, "Administrator"],
      "email_from": false,
      "partner_ids": [],
      "notification_ids": [],
      "starred": false,
      "needaction": false,
      "has_error": false,
      "create_uid": [2, "Administrator"],
      "create_date": "2026-03-22 01:00:51.153395"
    }
  },
  "message": "Record created in mail.message"
}
```

**Sales User Response (same structure):**

```json
{
  "success": true,
  "data": {
    "id": 343,
    "record": {
      "id": 343,
      "body": "<p>Sales update: Customer interested in new product</p>",
      "preview": "Sales update: Customer interested in new product",
      "model": "res.partner",
      "res_id": 9,
      "record_name": "Test Customer",
      "message_type": "comment",
      "subtype_id": [1, "Discussions"],
      "author_id": [8, "Sales User"],
      "email_from": "\"Sales User\" <sales@test.com>",
      "create_uid": [7, "Sales User"]
    }
  },
  "message": "Record created in mail.message"
}
```

**Regular User (without partner creation rights):**

```json
{
  "success": false,
  "error": {
    "message": "Error creating record",
    "code": "CREATE_ERROR"
  }
}
```

> Note: Creating messages requires write access to the target model. Regular Users without the Contact Creation group (`base.group_partner_manager`) cannot post messages on `res.partner`.

---

### 4. Activities (Tasks / Reminders)

#### `GET /api/v2/search/mail.activity` - List Activities

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?limit=10&fields=summary,note,date_deadline,user_id,activity_type_id,res_model,res_id,res_name,state"
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 1,
        "summary": "Follow up with client - UPDATED",
        "note": "<p>Call to discuss contract terms</p>",
        "date_deadline": "2026-03-25",
        "user_id": [2, "Administrator"],
        "activity_type_id": [2, "Call"],
        "res_model": "crm.lead",
        "res_id": 11,
        "res_name": "GreenEnergy Solutions's opportunity",
        "state": "planned"
      },
      {
        "id": 2,
        "summary": "Send proposal document",
        "note": "<p>Prepare and send proposal</p>",
        "date_deadline": "2026-03-26",
        "user_id": [6, "Regular User"],
        "activity_type_id": [1, "Email"],
        "res_model": "crm.lead",
        "res_id": 11,
        "res_name": "GreenEnergy Solutions's opportunity",
        "state": "planned"
      },
      {
        "id": 5,
        "summary": "Schedule product demo",
        "note": "<p>Demo for the new product line</p>",
        "date_deadline": "2026-03-28",
        "user_id": [7, "Sales User"],
        "activity_type_id": [3, "Meeting"],
        "res_model": "res.partner",
        "res_id": 9,
        "res_name": "Test Customer",
        "state": "planned"
      }
    ],
    "count": 3,
    "model": "mail.activity",
    "total_count": 3
  }
}
```

#### Filter: My activities

```bash
# Activities assigned to user_id 7 (Sales User)
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?user_id=7&fields=summary,date_deadline,activity_type_id,res_model,res_name,state"
```

#### Filter: By deadline (recommended over `state`)

The `state` field is computed and may not work as a search filter in all Odoo versions. Use `date_deadline` comparisons instead for reliable results:

```bash
# Overdue activities (deadline before today) - use date_deadline filter
# Note: The generic search only supports exact-match filters, so for
# date range queries, fetch all activities and filter client-side,
# or use the analytics endpoints which handle overdue detection.

# All activities for a user (then filter by date_deadline client-side)
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?user_id=2&fields=summary,date_deadline,activity_type_id,res_model,res_name,state"
```

#### Filter: By model

```bash
# Activities on CRM leads only
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?res_model=crm.lead&fields=summary,date_deadline,user_id,res_name,state"

# Activities on contacts/partners
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?res_model=res.partner&fields=summary,date_deadline,user_id,res_name,state"
```

#### `POST /api/v2/create/mail.activity` - Create Activity

To create an activity you need the `res_model_id` (from `ir.model`), not just the model name.

**Step 1: Find the `res_model_id`:**

```bash
# Get model ID for crm.lead
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/ir.model?model=crm.lead&fields=id,model"
# Returns: {"id": 512, "model": "crm.lead"}

# Get model ID for res.partner
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/ir.model?model=res.partner&fields=id,model"
# Returns: {"id": 90, "model": "res.partner"}
```

**Step 2: Create the activity:**

```bash
curl -X POST -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/create/mail.activity" \
  -d '{
    "summary": "Follow up with client",
    "note": "<p>Call to discuss contract terms</p>",
    "activity_type_id": 2,
    "date_deadline": "2026-03-25",
    "user_id": 2,
    "res_model_id": 512,
    "res_id": 11
  }'
```

**Activity type IDs:** 1=Email, 2=Call, 3=Meeting, 4=To-Do, 5=Document

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "id": 1,
    "record": {
      "id": 1,
      "display_name": "Follow up with client",
      "res_model_id": [512, "Lead"],
      "res_model": "crm.lead",
      "res_id": 11,
      "res_name": "GreenEnergy Solutions's opportunity",
      "activity_type_id": [2, "Call"],
      "activity_category": "phonecall",
      "icon": "fa-phone",
      "summary": "Follow up with client",
      "note": "<p>Call to discuss contract terms</p>",
      "date_deadline": "2026-03-25",
      "date_done": false,
      "feedback": false,
      "automated": false,
      "user_id": [2, "Administrator"],
      "state": "planned",
      "can_write": true,
      "active": true,
      "create_uid": [2, "Administrator"],
      "create_date": "2026-03-22 01:00:28.510847"
    }
  },
  "message": "Record created in mail.activity"
}
```

**Sales User Response (creating on res.partner):**

```json
{
  "success": true,
  "data": {
    "id": 5,
    "record": {
      "id": 5,
      "display_name": "Schedule product demo",
      "res_model_id": [90, "Contact"],
      "res_model": "res.partner",
      "res_id": 9,
      "res_name": "Test Customer",
      "activity_type_id": [3, "Meeting"],
      "activity_category": "meeting",
      "icon": "fa-users",
      "summary": "Schedule product demo",
      "note": "<p>Demo for the new product line</p>",
      "date_deadline": "2026-03-28",
      "user_id": [7, "Sales User"],
      "state": "planned",
      "can_write": true,
      "create_uid": [7, "Sales User"]
    }
  },
  "message": "Record created in mail.activity"
}
```

#### `PUT /api/v2/update/mail.activity/{id}` - Update Activity

```bash
curl -X PUT -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/update/mail.activity/1" \
  -d '{"summary": "Follow up with client - UPDATED"}'
```

**Admin Response:**

```json
{
  "success": true,
  "data": {
    "record": {
      "id": 1,
      "display_name": "Follow up with client - UPDATED",
      "write_date": "2026-03-22 01:01:05.698548"
    },
    "updated_fields": ["summary"]
  },
  "message": "Record 1 updated in mail.activity"
}
```

**Regular User (not the activity owner or without model access):**

```json
{
  "success": false,
  "error": {
    "message": "Error updating record",
    "code": "UPDATE_ERROR"
  }
}
```

#### `DELETE /api/v2/delete/mail.activity/{id}` - Delete Activity

```bash
curl -X DELETE -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/delete/mail.activity/1"
```

**Response (all roles can delete activities they have access to):**

```json
{
  "success": true,
  "data": {
    "id": 1,
    "model": "mail.activity"
  },
  "message": "Record 1 deleted from mail.activity"
}
```

---

### 5. Activity Types

#### `GET /api/v2/search/mail.activity.type` - List Activity Types

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity.type?limit=20&fields=name,summary,icon,category,delay_count,delay_unit,delay_from,res_model,chaining_type"
```

**Response (all roles see the same data):**

```json
{
  "success": true,
  "data": {
    "records": [
      {"id": 1, "name": "Email", "summary": "Email", "icon": "fa-envelope", "category": "default", "delay_count": 0, "delay_unit": "days", "delay_from": "previous_activity", "res_model": false, "chaining_type": "suggest"},
      {"id": 2, "name": "Call", "summary": "Call", "icon": "fa-phone", "category": "phonecall", "delay_count": 2, "delay_unit": "days", "delay_from": "previous_activity", "res_model": false, "chaining_type": "suggest"},
      {"id": 3, "name": "Meeting", "summary": "Meeting", "icon": "fa-users", "category": "meeting", "delay_count": 0, "delay_unit": "days", "delay_from": "previous_activity", "res_model": false, "chaining_type": "suggest"},
      {"id": 4, "name": "To-Do", "summary": "To-Do", "icon": "fa-check", "category": "default", "delay_count": 5, "delay_unit": "days", "delay_from": "previous_activity", "res_model": false, "chaining_type": "suggest"},
      {"id": 5, "name": "Document", "summary": "Document", "icon": "fa-upload", "category": "upload_file", "delay_count": 5, "delay_unit": "days", "delay_from": "previous_activity", "res_model": false, "chaining_type": "suggest"},
      {"id": 7, "name": "Certifications", "summary": "Upload a certification", "icon": "fa-upload", "category": "upload_file", "delay_count": 5, "delay_unit": "days", "delay_from": "previous_activity", "res_model": "hr.employee", "chaining_type": "suggest"}
    ],
    "count": 6,
    "total_count": 6
  }
}
```

---

### 6. Followers (Subscriptions)

#### `GET /api/v2/search/mail.followers` - List Followers

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.followers?limit=10&fields=partner_id,res_model,res_id,email,name,subtype_ids"
```

**Response (all roles):**

```json
{
  "success": true,
  "data": {
    "records": [
      {
        "id": 1,
        "partner_id": [3, "Administrator"],
        "res_model": "res.partner",
        "res_id": 9,
        "email": false,
        "name": "Administrator",
        "subtype_ids": [1]
      },
      {
        "id": 12,
        "partner_id": [3, "Administrator"],
        "res_model": "crm.lead",
        "res_id": 11,
        "email": false,
        "name": "Administrator",
        "subtype_ids": [1]
      },
      {
        "id": 13,
        "partner_id": [3, "Administrator"],
        "res_model": "sale.order",
        "res_id": 6,
        "email": false,
        "name": "Administrator",
        "subtype_ids": [1, 23]
      }
    ],
    "count": 10,
    "model": "mail.followers",
    "total_count": 11
  }
}
```

#### Filter: Followers for a specific record

```bash
# Who follows CRM lead 11?
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.followers?res_model=crm.lead&res_id=11&fields=partner_id,name,email,subtype_ids"

# Who follows partner 9?
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.followers?res_model=res.partner&res_id=9&fields=partner_id,name,email,subtype_ids"
```

---

### 7. Message Subtypes

#### `GET /api/v2/search/mail.message.subtype` - List Subtypes

```bash
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message.subtype?limit=30&fields=name,description,internal,hidden,default,res_model,sequence"
```

**Response (all roles):**

```json
{
  "success": true,
  "data": {
    "records": [
      {"id": 1, "name": "Discussions", "description": false, "internal": false, "hidden": false, "default": true, "res_model": false, "sequence": 0},
      {"id": 2, "name": "Note", "description": false, "internal": true, "hidden": false, "default": false, "res_model": false, "sequence": 100},
      {"id": 3, "name": "Activities", "description": false, "internal": true, "hidden": false, "default": false, "res_model": false, "sequence": 90},
      {"id": 5, "name": "Validated", "description": "Invoice validated", "internal": false, "hidden": false, "default": false, "res_model": "account.move", "sequence": 1},
      {"id": 6, "name": "Paid", "description": "Invoice paid", "internal": false, "hidden": false, "default": false, "res_model": "account.move", "sequence": 1},
      {"id": 9, "name": "Stage Changed", "description": "Stage changed", "internal": false, "hidden": false, "default": false, "res_model": "crm.lead", "sequence": 1},
      {"id": 10, "name": "Opportunity Won", "description": "Opportunity won", "internal": false, "hidden": false, "default": false, "res_model": "crm.lead", "sequence": 1},
      {"id": 11, "name": "Opportunity Lost", "description": "Opportunity lost", "internal": false, "hidden": false, "default": false, "res_model": "crm.lead", "sequence": 1}
    ],
    "count": 20,
    "total_count": 31
  }
}
```

---

## Quick Reference: Endpoint Access Matrix

| Endpoint | Method | Purpose | Admin | Internal User | Sales User |
|----------|--------|---------|-------|---------------|------------|
| `/api/v2/search/mail.notification` | GET | List notifications | All | All | All |
| `/api/v2/search/mail.message` | GET | List messages | All (257) | Filtered (89) | Filtered (89) |
| `/api/v2/search/mail.message/{id}` | GET | Get specific message | Yes | If accessible | If accessible |
| `/api/v2/create/mail.message` | POST | Post message/note | Yes | Model access required | Model access required |
| `/api/v2/search/mail.activity` | GET | List activities | All | Filtered by model | Filtered by model |
| `/api/v2/search/mail.activity?user_id=X` | GET | My activities | Yes | Yes | Yes |
| `/api/v2/search/mail.activity?state=overdue` | GET | Overdue activities | Yes | Yes | Yes |
| `/api/v2/create/mail.activity` | POST | Create activity | Yes | Model access required | Model access required |
| `/api/v2/update/mail.activity/{id}` | PUT | Update activity | Yes | Own activities | Own activities |
| `/api/v2/delete/mail.activity/{id}` | DELETE | Delete activity | Yes | Own activities | Own activities |
| `/api/v2/search/mail.activity.type` | GET | List activity types | All (6 types) | All (6 types) | All (6 types) |
| `/api/v2/search/mail.followers` | GET | List followers | All | All | All |
| `/api/v2/search/mail.followers?res_model=X&res_id=Y` | GET | Record followers | Yes | Yes | Yes |
| `/api/v2/search/mail.message.subtype` | GET | List subtypes | All (31) | All (31) | All (31) |

---

## Common Workflows

### Workflow 1: Build a Notification Inbox

```bash
# Step 1: Get messages that need action (inbox)
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?needaction=true&fields=subject,body,author_id,date,message_type,model,res_id,record_name"

# Step 2: Get unread notifications
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.notification?is_read=false&fields=mail_message_id,res_partner_id,notification_type,notification_status"

# Step 3: Get starred messages
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?starred=true&fields=subject,body,author_id,date,model,res_id,record_name"
```

### Workflow 2: Activity Dashboard

```bash
# Step 1: Get all my activities
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?user_id=YOUR_USER_ID&fields=summary,date_deadline,activity_type_id,res_model,res_name,state"

# Step 2: Get overdue activities (urgent)
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?state=overdue&fields=summary,date_deadline,user_id,res_model,res_name"

# Step 3: Get today's activities
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.activity?state=today&fields=summary,date_deadline,user_id,activity_type_id,res_name"

# Step 4: Create a new follow-up activity
curl -X POST -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/create/mail.activity" \
  -d '{
    "summary": "Follow up call",
    "activity_type_id": 2,
    "date_deadline": "2026-03-25",
    "user_id": YOUR_USER_ID,
    "res_model_id": MODEL_ID,
    "res_id": RECORD_ID
  }'
```

### Workflow 3: Record Chatter / Communication History

```bash
# Step 1: Get all messages for a specific record
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.message?model=res.partner&res_id=9&fields=subject,body,author_id,date,message_type,starred"

# Step 2: Post a new comment
curl -X POST -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/create/mail.message" \
  -d '{
    "body": "<p>Meeting went well. Client is interested.</p>",
    "message_type": "comment",
    "model": "res.partner",
    "res_id": 9,
    "subtype_id": 1
  }'

# Step 3: Post an internal note (employee-only)
curl -X POST -H "api-key: YOUR_API_KEY" -H "Content-Type: application/json" \
  "http://localhost:8069/api/v2/create/mail.message" \
  -d '{
    "body": "<p>Internal: credit check pending</p>",
    "message_type": "comment",
    "model": "res.partner",
    "res_id": 9,
    "subtype_id": 2
  }'

# Step 4: See who follows this record
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.followers?res_model=res.partner&res_id=9&fields=partner_id,name,email,subtype_ids"
```

### Workflow 4: Failed Notification Monitoring (Admin)

```bash
# Get failed email notifications
curl -H "api-key: YOUR_ADMIN_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.notification?notification_status=exception&fields=mail_message_id,res_partner_id,failure_type,failure_reason,mail_email_address"

# Get bounced notifications
curl -H "api-key: YOUR_ADMIN_API_KEY" \
  "http://localhost:8069/api/v2/search/mail.notification?notification_status=bounce&fields=mail_message_id,res_partner_id,failure_type,mail_email_address"
```

---

## Key Model IDs for Activity Creation

To create activities, you need the `res_model_id` from `ir.model`. Common ones:

```bash
# Look up any model ID
curl -H "api-key: YOUR_API_KEY" \
  "http://localhost:8069/api/v2/search/ir.model?model=MODEL_NAME&fields=id,model,name"
```

| Model | res_model_id | Name |
|-------|-------------|------|
| `res.partner` | 90 | Contact |
| `crm.lead` | 512 | Lead |

> Note: Model IDs may vary per database. Always look them up with the query above.
