# Odoo Development Environment Setup Guide

This guide walks you through setting up a complete Odoo development environment on macOS.

## Prerequisites

### 1. Install Postgres.app
1. Download [Postgres.app](https://postgresapp.com/) from the official website
2. Install and launch Postgres.app
3. Ensure PostgreSQL is running (you should see the elephant icon in your menu bar)

### 2. Install Development Tools
```bash
# Install Xcode command line tools
xcode-select --install
```

### 3. Install Homebrew (if not already installed)
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

## Step 1: Configure PostgreSQL Path

Add PostgreSQL binaries to your system PATH:

```bash
# Create paths directory and add PostgreSQL to PATH
sudo mkdir -p /etc/paths.d
echo /Applications/Postgres.app/Contents/Versions/latest/bin | sudo tee /etc/paths.d/postgresapp
```

**Note**: Restart your terminal or run `source ~/.bash_profile` for the PATH changes to take effect.

## Step 2: Set up PostgreSQL Database

### Create PostgreSQL User
```bash
# Create a PostgreSQL user with database creation privileges
sudo -u postgres createuser -d -R -S $USER
```

**If the above command fails**, try connecting directly to PostgreSQL:
```bash
# Connect to PostgreSQL directly
"/Applications/Postgres.app/Contents/Versions/17/bin/psql" -p5432 "postgres"
```

Then create the user within the PostgreSQL shell:
```sql
CREATE USER yourusername WITH CREATEDB;
\q
```

### Create Database
```bash
# Create a database with your username
createdb $USER
```

## Step 3: Set up Python Environment

### Install Python Environment Manager
```bash
# Install pyenv for Python version management
brew install pyenv
```

### Configure Python
```bash
# Check current Python version
python3 --version

# Upgrade pip to latest version
python3 -m pip install --upgrade pip
# OR if you have multiple Python versions:
/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 -m pip install --upgrade pip
```

### Configure Shell Profile
Add pyenv to your shell profile:
```bash
# Edit your bash profile
vim ~/.bash_profile
```

Add these lines to your `~/.bash_profile`:
```bash
# Add pyenv to PATH
export PATH="$HOME/.pyenv/bin:$PATH"
eval "$(pyenv init -)"
eval "$(pyenv virtualenv-init -)"
```

Then reload your profile:
```bash
source ~/.bash_profile
```

## Step 4: Navigate to Odoo Directory

```bash
# Navigate to your projects directory
cd /Users/projects/odoo

# Verify you're in the correct directory
pwd
ls
```

Your directory structure should look like:
```
/Users/projects/odoo/
├── addons/
├── odoo/
├── odoo-bin
├── requirements.txt
├── setup.py
└── ...
```

## Step 5: Install Python Dependencies

```bash
# Install essential Python packages
pip3 install setuptools wheel

# Install Odoo dependencies
pip3 install -r requirements.txt
```

**If you encounter installation errors**, try:
```bash
# Update pip first
pip3 install --upgrade pip

# Then retry installation
pip3 install setuptools wheel
pip3 install -r requirements.txt
```

## Step 6: Install Node.js Dependencies

```bash
# Install rtlcss globally for right-to-left language support
sudo npm install -g rtlcss
```

**Note**: If you don't have Node.js installed, install it first:
```bash
brew install node
```

## Step 7: Start Odoo

### Basic Start Command
```bash
# Start Odoo with basic configuration
python3 odoo-bin
```

### Start with Custom Database
```bash
# Start Odoo with specific addons path and database
python3 odoo-bin --addons-path=addons -d mydb
```

### Complete Start Command with Options
```bash
# Start with full configuration
python3 odoo-bin --addons-path=addons -d mydb --db-host=localhost --db-port=5432 --db-user=$USER
```

## Step 8: Access Odoo

1. Open your web browser
2. Navigate to: `http://localhost:8069`
3. Create your first database or use the one specified (`mydb`)
4. Set up your admin account

## Common Start Commands

### Development Mode
```bash
# Start in development mode with auto-reload
python3 odoo-bin --addons-path=addons -d mydb --dev=all
```

### With Specific Port
```bash
# Start on a different port
python3 odoo-bin --addons-path=addons -d mydb --xmlrpc-port=8070
```

### Update Module
```bash
# Start and update a specific module
python3 odoo-bin --addons-path=addons -d mydb -u module_name
```

### Install Module
```bash
# Start and install a specific module
python3 odoo-bin --addons-path=addons -d mydb -i module_name
```

## Troubleshooting

### PostgreSQL Connection Issues
```bash
# Check if PostgreSQL is running
ps aux | grep postgres

# Test PostgreSQL connection
psql -h localhost -p 5432 -U $USER -d postgres
```

### Python Dependencies Issues
```bash
# Create a virtual environment (recommended)
python3 -m venv odoo-venv
source odoo-venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Permission Issues
```bash
# Fix PostgreSQL user permissions
sudo -u postgres psql
ALTER USER yourusername CREATEDB;
\q
```

### Port Already in Use
```bash
# Check what's using port 8069
lsof -i :8069

# Kill the process if needed
kill -9 PID
```

## Configuration File (Optional)

Create a configuration file for easier management:

```bash
# Create config file
vim odoo.conf
```

Add the following content:
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

## Development Tips

### Enable Developer Mode
1. Go to Settings → Activate Developer Mode
2. Or append `?debug=1` to any Odoo URL

### Useful Development Commands
```bash
# Start with specific logging
python3 odoo-bin --addons-path=addons -d mydb --log-level=debug

# Start with database management enabled
python3 odoo-bin --addons-path=addons --list-db

# Start without demo data
python3 odoo-bin --addons-path=addons -d mydb --without-demo=all
```

### File Structure for Custom Addons
```
addons/
├── your_custom_addon/
│   ├── __init__.py
│   ├── __manifest__.py
│   ├── models/
│   ├── views/
│   ├── static/
│   └── security/
```

## Next Steps

1. Explore the Odoo documentation: https://www.odoo.com/documentation/
2. Check out the developer tutorials
3. Start building your first custom module
4. Consider setting up a proper development environment with IDE integration

## Quick Reference

| Command | Description |
|---------|-------------|
| `python3 odoo-bin` | Start Odoo with default settings |
| `python3 odoo-bin -d mydb` | Start with specific database |
| `python3 odoo-bin --dev=all` | Start in development mode |
| `python3 odoo-bin -i module_name` | Install a module |
| `python3 odoo-bin -u module_name` | Update a module |
| `python3 odoo-bin --list-db` | Enable database management |

---

**Happy coding with Odoo! 🚀** 