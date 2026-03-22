# Odoo Installation Guide

This guide covers setting up an Odoo 19 development environment. Choose between **Docker** (recommended for quick setup) or a **native macOS** installation.

---

## Option A: Docker Installation (Recommended)

The fastest way to get Odoo running. Requires only Docker — no need to install Python, PostgreSQL, or Node.js on your host machine.

### Prerequisites

- **Docker Desktop**: [Install Docker Desktop](https://www.docker.com/products/docker-desktop/)

Verify Docker is installed:

```bash
docker --version
docker compose version
```

### Quick Start

From the project root:

```bash
# Optional: customize database settings
cp .env.example .env
# Edit .env if needed (e.g. ODOO_DB, ODOO_INIT_MODULES)

# Build and start Odoo + PostgreSQL
docker compose up -d

# Initialize database and install modules (required on first run)
# Uses ODOO_INIT_MODULES from .env (see "Module Presets" below)
docker compose exec odoo python3 /opt/odoo/odoo-bin \
  -c /etc/odoo/odoo.conf -d ${ODOO_DB:-odoo19_db} \
  -i ${ODOO_INIT_MODULES:-base,base_api} --stop-after-init

# Restart Odoo to pick up the initialized database
docker compose restart odoo
```

That's it. Open **http://localhost:8069** and log in:
- **Email**: `admin`
- **Password**: `admin`

### Module Presets

Set `ODOO_INIT_MODULES` in your `.env` file to control which modules are installed on first init.

**Minimal** — just the API, nothing else:
```
ODOO_INIT_MODULES=base,base_api
```

**Standard** — core business apps + API:
```
ODOO_INIT_MODULES=base,base_api,sale,crm,hr,purchase,stock,account
```

**Full** (default — matches local dev environment, 64 modules):
```
ODOO_INIT_MODULES=base,base_api,api_doc,account,account_add_gln,account_edi_ubl_cii,account_payment,analytic,auth_passkey,auth_passkey_portal,auth_signup,auth_totp,auth_totp_mail,auth_totp_portal,base_import,base_import_module,base_install_request,base_setup,bus,calendar,certificate,contacts,crm,digest,google_gmail,hr,hr_calendar,hr_homeworking,hr_homeworking_calendar,hr_org_chart,hr_skills,html_editor,http_routing,l10n_fr,l10n_us,mail,mail_bot,mail_bot_hr,microsoft_outlook,onboarding,payment,phone_validation,portal,privacy_lookup,product,resource,resource_mail,rpc,sale,sale_crm,sale_edi_ubl,sale_management,sale_pdf_quote_builder,sales_team,spreadsheet,spreadsheet_account,spreadsheet_dashboard,spreadsheet_dashboard_account,spreadsheet_dashboard_sale,uom,utm,web,web_hierarchy,web_tour,web_unsplash
```

#### Adding More Modules

To install additional modules on an already-running Docker instance:

```bash
docker compose exec odoo python3 /opt/odoo/odoo-bin \
  -c /etc/odoo/odoo.conf -d ${ODOO_DB:-odoo19_db} -i module1,module2 --stop-after-init
docker compose restart odoo
```

#### Available Modules by Category

Below are the most useful uninstalled modules you can add. Use the module name in `-i`.

**Inventory & Logistics**

| Module | Name | Description |
|--------|------|-------------|
| `stock` | Inventory | Warehouse management |
| `purchase` | Purchase | Purchase orders & procurement |
| `delivery` | Delivery Costs | Shipping cost management |
| `stock_picking_batch` | Batch Transfer | Bulk warehouse operations |
| `stock_landed_costs` | Landed Costs | Additional cost tracking |

**Website & eCommerce**

| Module | Name | Description |
|--------|------|-------------|
| `website` | Website | Website builder |
| `website_sale` | eCommerce | Online store |
| `website_blog` | Blog | Blog engine |
| `website_event` | Events | Online event management |
| `website_forum` | Forum | Community forum |
| `website_slides` | eLearning | Online courses |
| `website_crm` | Contact Form | CRM web forms |
| `website_hr_recruitment` | Online Jobs | Job posting portal |

**HR & People**

| Module | Name | Description |
|--------|------|-------------|
| `hr_holidays` | Time Off | Leave management |
| `hr_expense` | Expenses | Expense reporting |
| `hr_attendance` | Attendances | Clock in/out tracking |
| `hr_recruitment` | Recruitment | Hiring pipeline |
| `hr_timesheet` | Task Logs | Time tracking |
| `hr_fleet` | Fleet History | Vehicle management |
| `hr_work_entry` | Work Entries | Payroll work entries |

**Project & Services**

| Module | Name | Description |
|--------|------|-------------|
| `project` | Project | Project & task management |
| `hr_timesheet` | Timesheets | Time tracking on tasks |

**Marketing & Communication**

| Module | Name | Description |
|--------|------|-------------|
| `mass_mailing` | Email Marketing | Bulk email campaigns |
| `mass_mailing_sms` | SMS Marketing | Bulk SMS campaigns |
| `im_livechat` | Live Chat | Real-time chat support |
| `crm_sms` | CRM SMS | SMS from CRM |
| `survey` | Surveys | Online surveys & forms |

**Manufacturing**

| Module | Name | Description |
|--------|------|-------------|
| `mrp` | Manufacturing | Bill of materials & work orders |
| `mrp_subcontracting` | Subcontracting | Outsourced manufacturing |
| `maintenance` | Maintenance | Equipment maintenance |

**Accounting Extras**

| Module | Name | Description |
|--------|------|-------------|
| `account_check_printing` | Check Printing | Print checks |
| `account_debit_note` | Debit Notes | Create debit notes |
| `account_tax_python` | Tax Python | Taxes as Python code |
| `base_vat` | VAT Validation | VAT number validation |
| `base_iban` | IBAN | IBAN bank account support |

**Authentication & Security**

| Module | Name | Description |
|--------|------|-------------|
| `auth_oauth` | OAuth2 | OAuth2 login (Google, etc.) |
| `auth_ldap` | LDAP | LDAP authentication |
| `auth_password_policy` | Password Policy | Password strength rules |

**Other Useful Modules**

| Module | Name | Description |
|--------|------|-------------|
| `base_automation` | Automation Rules | Automated actions |
| `board` | Dashboards | Custom dashboards |
| `fleet` | Fleet | Vehicle management |
| `gamification` | Gamification | Goals & badges |
| `data_recycle` | Data Recycle | Clean up old records |

#### Example: Install Website + eCommerce

```bash
docker compose exec odoo python3 /opt/odoo/odoo-bin \
  -c /etc/odoo/odoo.conf -d ${ODOO_DB:-odoo19_db} -i website,website_sale --stop-after-init
docker compose restart odoo
```

### Migrating Data from Local to Docker

If you have an existing local PostgreSQL database you want to bring into Docker:

```bash
# 1. Dump local database (plain SQL — works across PG versions)
pg_dump --format=plain --no-owner --no-privileges odoo19_db | gzip > /tmp/odoo19_db.sql.gz

# 2. Stop Odoo (keep DB container running)
docker compose stop odoo

# 3. Copy dump into the DB container
docker cp /tmp/odoo19_db.sql.gz odoo-db-1:/tmp/odoo19_db.sql.gz

# 4. Drop old Docker DB, recreate, and restore
docker compose exec db bash -c "\
  dropdb -U odoo --if-exists odoo19_db && \
  createdb -U odoo -O odoo odoo19_db && \
  gunzip -c /tmp/odoo19_db.sql.gz | psql -U odoo -d odoo19_db -q"

# 5. Start Odoo
docker compose start odoo
```

> **Note**: Use plain-text SQL dumps (`--format=plain`) when local and Docker PostgreSQL major versions differ. Binary dumps (`-Fc`) only work when `pg_restore` version >= the dump version.

### What Docker Compose Starts

| Service      | Image         | Port (host) | Description                     |
|-------------|---------------|-------------|---------------------------------|
| `db`        | postgres:18   | 5433        | PostgreSQL database             |
| `odoo`      | built locally | 8069        | Odoo web server                 |

### Common Docker Commands

```bash
# Start the stack
docker compose up -d

# Stop the stack
docker compose down

# View live Odoo logs
docker compose exec odoo tail -f /var/log/odoo/odoo.log

# Check container status
docker compose ps

# Rebuild after code changes
docker compose up -d --build

# Install a module
docker compose exec odoo python3 /opt/odoo/odoo-bin \
  -c /etc/odoo/odoo.conf -d ${ODOO_DB:-odoo19_db} -i module_name --stop-after-init
docker compose restart odoo

# Update a module
docker compose exec odoo python3 /opt/odoo/odoo-bin \
  -c /etc/odoo/odoo.conf -d ${ODOO_DB:-odoo19_db} -u module_name --stop-after-init
docker compose restart odoo

# Open a shell inside the Odoo container
docker compose exec odoo bash

# Connect to the PostgreSQL database
docker compose exec db psql -U ${ODOO_DB_USER:-odoo} -d ${ODOO_DB:-odoo19_db}

# Reset everything (removes all data)
docker compose down -v
```

### Docker Configuration

The Docker setup uses these files:

| File                    | Purpose                                          |
|------------------------|--------------------------------------------------|
| `Dockerfile`           | Builds the Odoo image from source                |
| `docker-compose.yml`   | Orchestrates Odoo + PostgreSQL services           |
| `docker/odoo.conf`     | Odoo server configuration for Docker              |
| `.dockerignore`        | Excludes unnecessary files from the Docker build  |
| `.env`                 | Optional overrides (database, users, init modules) |

To change Odoo settings (port, log level, worker count, etc.), edit `docker/odoo.conf` and rebuild:

```bash
docker compose up -d --build
```

### Docker Volumes

Data persists across restarts via named volumes:

| Volume             | Mounted at (container)                    | Content           |
|-------------------|-------------------------------------------|--------------------|
| `odoo-db-data`    | `/var/lib/postgresql/data/pgdata`         | PostgreSQL data    |
| `odoo-filestore`  | `/var/lib/odoo/filestore`                 | Odoo file uploads  |
| `odoo-logs`       | `/var/log/odoo`                           | Odoo log files     |

### Docker Troubleshooting

**Port 8069 already in use:**
```bash
# Check what's using the port
lsof -i :8069

# Or change the host port in docker-compose.yml
# e.g., "8070:8069" to use port 8070 on the host
```

**Database not initialized (500 error on first visit):**
```bash
docker compose exec odoo python3 /opt/odoo/odoo-bin \
  -c /etc/odoo/odoo.conf -d ${ODOO_DB:-odoo19_db} \
  -i ${ODOO_INIT_MODULES:-base,base_api} --stop-after-init
docker compose restart odoo
```

**Rebuild from scratch:**
```bash
docker compose down -v
docker compose up -d --build
# Then re-initialize the database (see Quick Start)
```

---

## Option B: Native macOS Installation

This section follows the official Odoo installation documentation for setting up a development environment directly on macOS.

### System Requirements

- **Python**: 3.10 to 3.13 (required by Odoo 19)
- **PostgreSQL**: 13 or above (required by Odoo 19)
- **Node.js**: Required for rtlcss package

### Step 1: Install Python

#### Using Package Manager (Recommended)

Use Homebrew or MacPorts to install Python 3:

```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Python 3
brew install python3
```

#### Verify Python Installation

Check that Python 3.10 or later is installed:

```bash
python3 --version
```

**Expected output**: `Python 3.10.x` or higher

#### Verify pip Installation

Ensure pip is available for this Python version:

```bash
pip3 --version
```

> **Note**: If Python 3 is already installed, make sure that the version is 3.10 or above, as previous versions are not compatible with Odoo.

### Step 2: Install PostgreSQL

#### Install via Homebrew

```bash
# Install PostgreSQL (version 16 recommended)
brew install postgresql@16

# Start PostgreSQL as a background service
brew services start postgresql@16
```

#### Verify PostgreSQL is Running

```bash
# Check service status
brew services list | grep postgresql

# Verify connection
psql -l
```

#### Create PostgreSQL User (if needed)

Homebrew's PostgreSQL installation creates a superuser role matching your macOS username, so you can typically connect without extra setup. If you need a separate user:

```bash
# Create a new PostgreSQL user with database creation rights
createuser -d -R -S odoo_user

# Create a database with your username (if it does not already exist)
createdb $USER
```

> **Note**: With Homebrew PostgreSQL, the current macOS user can connect without a password by default (peer authentication).

### Step 3: Install Dependencies

#### Navigate to Odoo Directory

```bash
# Navigate to your Odoo Community installation path
cd /Users/projects/odoo
```

#### Install Python Dependencies

Odoo dependencies are listed in the `requirements.txt` file located at the root of the Odoo Community directory.

```bash
# Install essential Python packages
pip3 install setuptools wheel

# Install Odoo requirements
pip3 install -r requirements.txt
```

> **Tip**: It can be preferable not to mix Python module packages between different instances of Odoo or with the system. You can use virtualenv to create isolated Python environments:
> 
> ```bash
> python3 -m venv odoo-env
> source odoo-env/bin/activate
> pip3 install setuptools wheel
> pip3 install -r requirements.txt
> ```

#### Install Non-Python Dependencies

Install Command Line Tools:

```bash
xcode-select --install
```

#### Install Node.js and rtlcss

For languages using a right-to-left interface (such as Arabic or Hebrew), the rtlcss package is required.

```bash
# Install Node.js using Homebrew
brew install node

# Install rtlcss globally
sudo npm install -g rtlcss
```

> **Warning**: Non-Python dependencies must be installed with a package manager (Homebrew, MacPorts).

#### Optional: Install wkhtmltopdf

> **Warning**: wkhtmltopdf is not installed through pip and must be installed manually in version 0.12.6 for it to support headers and footers. Check out the [wkhtmltopdf wiki](https://wkhtmltopdf.org/) for more details on the various versions.

```bash
# Install wkhtmltopdf using Homebrew
brew install wkhtmltopdf
```

### Step 4: Running Odoo

Once all dependencies are set up, Odoo can be launched by running `odoo-bin`, the command-line interface of the server. It is located at the root of the Odoo Community directory.

#### Basic Start Command

```bash
# Navigate to Odoo Community path
cd /Users/projects/odoo

# Start Odoo
python3 odoo-bin --addons-path=addons -d mydb
```

Where:
- `/Users/projects/odoo` is the path of the Odoo Community installation
- `mydb` is the name of the PostgreSQL database

#### Common Configuration Options

To configure the server, you can specify command-line arguments or use a configuration file.

**Common necessary configurations include:**
- PostgreSQL user and password
- Custom addon paths beyond the defaults to load custom modules

#### Enterprise Edition

> **Tip**: For the Enterprise edition, add the path to the enterprise add-ons to the `addons-path` argument. Note that it must come before the other paths in `addons-path` for add-ons to be loaded correctly.

```bash
python3 odoo-bin --addons-path=enterprise,addons -d mydb
```

### Step 5: Access Odoo

1. After the server has started (look for the log message: `INFO odoo.modules.loading: Modules loaded.`)
2. Open your web browser
3. Navigate to: `http://localhost:8069`
4. Log into the Odoo database with the base administrator account:
   - **Email**: `admin`
   - **Password**: `admin`

> **Tip**: From there, create and manage new users. The user account used to log into Odoo's web interface differs from the `--db_user` CLI argument.

### Common Start Commands

#### Development Mode
```bash
python3 odoo-bin --addons-path=addons -d mydb --dev=all
```

#### Custom Database Configuration
```bash
python3 odoo-bin --addons-path=addons -d mydb --db-host=localhost --db-port=5432 --db-user=$USER
```

#### Install Specific Module
```bash
python3 odoo-bin --addons-path=addons -d mydb -i module_name
```

#### Update Specific Module
```bash
python3 odoo-bin --addons-path=addons -d mydb -u module_name
```

#### Custom Port
```bash
python3 odoo-bin --addons-path=addons -d mydb --xmlrpc-port=8070
```

#### With Logging
```bash
python3 odoo-bin --addons-path=addons -d mydb --log-level=debug
```

### Configuration File (Optional)

You can create a configuration file instead of using command-line arguments:

```bash
# Create configuration file
touch odoo.conf
```

Example `odoo.conf` content:
```ini
[options]
addons_path = addons
admin_passwd = admin
db_host = localhost
db_port = 5432
db_user = yourusername
db_password = 
xmlrpc_port = 8069
logfile = odoo.log
log_level = info
```

Then start Odoo with:
```bash
python3 odoo-bin -c odoo.conf -d mydb
```

### Multi-Company Setup: Running 2 Databases for 2 Companies

This section provides a complete example of setting up two separate Odoo databases for two different companies, each running on different ports.

### Scenario Setup
- **Company A**: "TechCorp" - Database: `techcorp_db` - Port: `8069`
- **Company B**: "RetailPlus" - Database: `retailplus_db` - Port: `8070`

### Step 1: Create the Databases

```bash
# Navigate to Odoo directory
cd /Users/projects/odoo

# Create first database for TechCorp
createdb techcorp_db

# Create second database for RetailPlus
createdb retailplus_db

# Verify databases were created
psql -l | grep -E "(techcorp_db|retailplus_db)"
```

### Step 2: Create Configuration Files

Create separate configuration files for each company:

#### Configuration for TechCorp (techcorp.conf)

```bash
# Create TechCorp configuration file
cat > techcorp.conf << EOF
[options]
# Database settings
db_host = localhost
db_port = 5432
db_user = $USER
db_password = 
dbfilter = ^techcorp_db$

# Server settings
xmlrpc_port = 8069
longpolling_port = 8072

# Paths
addons_path = addons
data_dir = ./filestore/techcorp

# Logging
logfile = ./logs/techcorp.log
log_level = info

# Security
admin_passwd = techcorp_admin_2024

# Performance
workers = 0
max_cron_threads = 1

# Development
dev_mode = reload,qweb,werkzeug,xml
EOF
```

#### Configuration for RetailPlus (retailplus.conf)

```bash
# Create RetailPlus configuration file
cat > retailplus.conf << EOF
[options]
# Database settings
db_host = localhost
db_port = 5432
db_user = $USER
db_password = 
dbfilter = ^retailplus_db$

# Server settings
xmlrpc_port = 8070
longpolling_port = 8073

# Paths
addons_path = addons
data_dir = ./filestore/retailplus

# Logging
logfile = ./logs/retailplus.log
log_level = info

# Security
admin_passwd = retailplus_admin_2024

# Performance
workers = 0
max_cron_threads = 1

# Development
dev_mode = reload,qweb,werkzeug,xml
EOF
```

### Step 3: Create Directory Structure

```bash
# Create directories for file storage and logs
mkdir -p filestore/techcorp
mkdir -p filestore/retailplus
mkdir -p logs

# Set proper permissions
chmod 755 filestore/techcorp filestore/retailplus logs
```

### Step 4: Initialize the Databases

#### Initialize TechCorp Database

```bash
# Start Odoo with TechCorp config and initialize base modules
python3 odoo-bin -c techcorp.conf -d techcorp_db -i base --stop-after-init

# Install additional modules for a tech company
python3 odoo-bin -c techcorp.conf -d techcorp_db -i project,hr,website,sale,purchase --stop-after-init
```

#### Initialize RetailPlus Database

```bash
# Start Odoo with RetailPlus config and initialize base modules
python3 odoo-bin -c retailplus.conf -d retailplus_db -i base --stop-after-init

# Install additional modules for a retail company
python3 odoo-bin -c retailplus.conf -d retailplus_db -i point_of_sale,stock,sale,purchase,account --stop-after-init
```

### Step 5: Start Both Instances

#### Option 1: Start Both Instances Manually

**Terminal 1 - Start TechCorp Instance:**
```bash
cd /Users/projects/odoo
python3 odoo-bin -c techcorp.conf -d techcorp_db
```

**Terminal 2 - Start RetailPlus Instance:**
```bash
cd /Users/projects/odoo
python3 odoo-bin -c retailplus.conf -d retailplus_db
```

#### Option 2: Create Startup Scripts

**Create startup script for TechCorp:**
```bash
cat > start_techcorp.sh << 'EOF'
#!/bin/bash
cd /Users/projects/odoo
echo "Starting TechCorp Odoo instance..."
python3 odoo-bin -c techcorp.conf -d techcorp_db
EOF

chmod +x start_techcorp.sh
```

**Create startup script for RetailPlus:**
```bash
cat > start_retailplus.sh << 'EOF'
#!/bin/bash
cd /Users/projects/odoo
echo "Starting RetailPlus Odoo instance..."
python3 odoo-bin -c retailplus.conf -d retailplus_db
EOF

chmod +x start_retailplus.sh
```

#### Option 3: Start Both with Background Processes

```bash
# Start TechCorp in background
nohup python3 odoo-bin -c techcorp.conf -d techcorp_db > logs/techcorp_startup.log 2>&1 &
TECHCORP_PID=$!
echo "TechCorp started with PID: $TECHCORP_PID"

# Start RetailPlus in background
nohup python3 odoo-bin -c retailplus.conf -d retailplus_db > logs/retailplus_startup.log 2>&1 &
RETAILPLUS_PID=$!
echo "RetailPlus started with PID: $RETAILPLUS_PID"

# Save PIDs for later management
echo $TECHCORP_PID > techcorp.pid
echo $RETAILPLUS_PID > retailplus.pid

echo "Both instances started successfully!"
echo "TechCorp: http://localhost:8069"
echo "RetailPlus: http://localhost:8070"
```

### Step 6: Access the Instances

#### TechCorp Access
- **URL**: `http://localhost:8069`
- **Database**: `techcorp_db`
- **Admin Email**: `admin`
- **Admin Password**: `admin`

#### RetailPlus Access
- **URL**: `http://localhost:8070`
- **Database**: `retailplus_db`
- **Admin Email**: `admin`
- **Admin Password**: `admin`

### Step 7: Management Commands

#### Check Running Instances
```bash
# Check if instances are running
ps aux | grep odoo-bin | grep -v grep

# Check specific ports
lsof -i :8069  # TechCorp
lsof -i :8070  # RetailPlus
```

#### Stop Instances
```bash
# If running in background, use PIDs
kill $(cat techcorp.pid)
kill $(cat retailplus.pid)

# Or find and kill by port
kill $(lsof -ti:8069)
kill $(lsof -ti:8070)

# Clean up PID files
rm -f techcorp.pid retailplus.pid
```

#### Restart Instances
```bash
# Restart TechCorp
kill $(cat techcorp.pid) 2>/dev/null
nohup python3 odoo-bin -c techcorp.conf -d techcorp_db > logs/techcorp_startup.log 2>&1 &
echo $! > techcorp.pid

# Restart RetailPlus
kill $(cat retailplus.pid) 2>/dev/null
nohup python3 odoo-bin -c retailplus.conf -d retailplus_db > logs/retailplus_startup.log 2>&1 &
echo $! > retailplus.pid
```

### Step 8: Advanced Multi-Company Management

#### Create Master Management Script

```bash
cat > manage_companies.sh << 'EOF'
#!/bin/bash

TECHCORP_CONFIG="techcorp.conf"
RETAILPLUS_CONFIG="retailplus.conf"
TECHCORP_DB="techcorp_db"
RETAILPLUS_DB="retailplus_db"

case "$1" in
    start)
        echo "Starting both company instances..."
        nohup python3 odoo-bin -c $TECHCORP_CONFIG -d $TECHCORP_DB > logs/techcorp_startup.log 2>&1 &
        echo $! > techcorp.pid
        echo "TechCorp started (PID: $(cat techcorp.pid))"
        
        nohup python3 odoo-bin -c $RETAILPLUS_CONFIG -d $RETAILPLUS_DB > logs/retailplus_startup.log 2>&1 &
        echo $! > retailplus.pid
        echo "RetailPlus started (PID: $(cat retailplus.pid))"
        
        echo "Access URLs:"
        echo "  TechCorp: http://localhost:8069"
        echo "  RetailPlus: http://localhost:8070"
        ;;
    stop)
        echo "Stopping both company instances..."
        if [ -f techcorp.pid ]; then
            kill $(cat techcorp.pid) 2>/dev/null && echo "TechCorp stopped"
            rm -f techcorp.pid
        fi
        if [ -f retailplus.pid ]; then
            kill $(cat retailplus.pid) 2>/dev/null && echo "RetailPlus stopped"
            rm -f retailplus.pid
        fi
        ;;
    status)
        echo "Checking instance status..."
        if [ -f techcorp.pid ] && kill -0 $(cat techcorp.pid) 2>/dev/null; then
            echo "TechCorp: RUNNING (PID: $(cat techcorp.pid))"
        else
            echo "TechCorp: STOPPED"
        fi
        if [ -f retailplus.pid ] && kill -0 $(cat retailplus.pid) 2>/dev/null; then
            echo "RetailPlus: RUNNING (PID: $(cat retailplus.pid))"
        else
            echo "RetailPlus: STOPPED"
        fi
        ;;
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
    logs)
        echo "Recent logs for both instances:"
        echo "=== TechCorp Logs ==="
        tail -n 10 logs/techcorp.log 2>/dev/null || echo "No TechCorp logs found"
        echo ""
        echo "=== RetailPlus Logs ==="
        tail -n 10 logs/retailplus.log 2>/dev/null || echo "No RetailPlus logs found"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        echo ""
        echo "Commands:"
        echo "  start   - Start both company instances"
        echo "  stop    - Stop both company instances"
        echo "  restart - Restart both company instances"
        echo "  status  - Check if instances are running"
        echo "  logs    - Show recent logs from both instances"
        exit 1
        ;;
esac
EOF

chmod +x manage_companies.sh
```

#### Usage Examples

```bash
# Start both companies
./manage_companies.sh start

# Check status
./manage_companies.sh status

# View recent logs
./manage_companies.sh logs

# Restart both instances
./manage_companies.sh restart

# Stop both instances
./manage_companies.sh stop
```

### Step 9: Database Backup and Maintenance

#### Backup Both Databases
```bash
# Create backup directory
mkdir -p backups/$(date +%Y-%m-%d)

# Backup TechCorp database
pg_dump techcorp_db > backups/$(date +%Y-%m-%d)/techcorp_db_backup.sql

# Backup RetailPlus database
pg_dump retailplus_db > backups/$(date +%Y-%m-%d)/retailplus_db_backup.sql

echo "Backups created in backups/$(date +%Y-%m-%d)/"
```

#### Automated Backup Script
```bash
cat > backup_databases.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="backups/$(date +%Y-%m-%d)"
mkdir -p $BACKUP_DIR

echo "Creating database backups..."
pg_dump techcorp_db > $BACKUP_DIR/techcorp_db_$(date +%H%M).sql
pg_dump retailplus_db > $BACKUP_DIR/retailplus_db_$(date +%H%M).sql

echo "Backups completed in $BACKUP_DIR"
ls -la $BACKUP_DIR
EOF

chmod +x backup_databases.sh
```

### Summary

You now have a complete multi-company Odoo setup with:

✅ **Two separate databases** for different companies
✅ **Individual configuration files** with different ports
✅ **Isolated file storage** for each company
✅ **Management scripts** for easy operation
✅ **Backup procedures** for data protection

**Quick Access:**
- **TechCorp**: http://localhost:8069
- **RetailPlus**: http://localhost:8070

Use `./manage_companies.sh start` to begin working with both companies!

---

## Troubleshooting

### Python Version Issues
```bash
# Check Python version
python3 --version

# If version is below 3.10, update Python
brew upgrade python3
```

### PostgreSQL Connection Issues
```bash
# Check if PostgreSQL is running
ps aux | grep postgres

# Test connection
psql -h localhost -p 5432 -U $USER -d $USER
```

### Dependencies Installation Issues
```bash
# Update pip
pip3 install --upgrade pip

# Reinstall dependencies
pip3 install setuptools wheel
pip3 install -r requirements.txt
```

### Port Already in Use
```bash
# Check what's using port 8069
lsof -i :8069

# Kill the process if needed
kill -9 PID
```

## Virtual Environment Setup (Recommended)

To avoid conflicts between different Python projects:

```bash
# Create virtual environment
python3 -m venv odoo-env

# Activate virtual environment
source odoo-env/bin/activate

# Install dependencies in virtual environment
pip3 install setuptools wheel
pip3 install -r requirements.txt

# When done, deactivate
deactivate
```

## Development Tips

### Enable Developer Mode
1. Access Odoo at `http://localhost:8069`
2. Go to Settings → Activate Developer Mode
3. Or append `?debug=1` to any Odoo URL

### Useful CLI Arguments
- `--dev=all`: Enable development mode with auto-reload
- `--list-db`: Enable database management interface
- `--without-demo=all`: Start without demo data
- `--test-enable`: Enable testing
- `--workers=0`: Disable multiprocessing (useful for debugging)

## Next Steps

1. **Create your first module**: Follow the [Odoo development tutorials](https://www.odoo.com/documentation/master/developer/howtos.html)
2. **Explore the codebase**: Familiarize yourself with the addon structure
3. **Set up your IDE**: Configure your development environment for Python/JavaScript
4. **Read the documentation**: Check the [official Odoo documentation](https://www.odoo.com/documentation/)

## Quick Reference

| Command | Description |
|---------|-------------|
| `python3 odoo-bin` | Start Odoo with default settings |
| `python3 odoo-bin -d mydb` | Start with specific database |
| `python3 odoo-bin --dev=all` | Start in development mode |
| `python3 odoo-bin -i module_name` | Install a module |
| `python3 odoo-bin -u module_name` | Update a module |
| `python3 odoo-bin --list-db` | Enable database management |
| `python3 odoo-bin -c config.conf` | Start with configuration file |

---

> **See also**: [The complete list of CLI arguments for odoo-bin](https://www.odoo.com/documentation/master/developer/reference/cli.html)

**Happy developing with Odoo! 🚀** 