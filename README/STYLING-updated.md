### UI Styling Guide (Colors, Fonts, and Overrides)

This guide explains where to change styles for the backend (web client) and the website (frontend), how to include custom SCSS, and how to load custom fonts. **Includes considerations for API documentation and developer tools.**

## Choose your target

- Backend (internal web client): add assets to `web.assets_backend`
- Website (public site): add assets to `web.assets_frontend`
- **API Documentation UI**: custom styling for API docs and developer portals

Do not modify `addons/web` directly. Create or use your own addon to provide overrides.

## Minimal addon setup

`addons/ui_overrides/__manifest__.py`
```python
{
  "name": "UI Overrides",
  "version": "1.0",
  "depends": ["web"],  # use ["website"] if you target the website frontend
  "assets": {
    "web.assets_backend": [
      "ui_overrides/static/src/scss/overrides.scss",
    ],
    # For website/frontend instead, use:
    # "web.assets_frontend": [
    #   "ui_overrides/static/src/scss/overrides.scss",
    # ],
    
    # For API documentation styling
    "web.assets_frontend": [
      "ui_overrides/static/src/scss/api_docs.scss",
    ],
  },
}
```

Directory structure:
```
addons/
  ui_overrides/
    __manifest__.py
    __init__.py
    static/
      src/
        scss/
          overrides.scss
          api_docs.scss       # NEW: API documentation styling
        fonts/
          MyFont.woff2
        js/
          api_explorer.js     # NEW: API testing widget
        templates/
          api_docs.xml        # NEW: API documentation templates
```

## Custom fonts

`addons/ui_overrides/static/src/scss/overrides.scss`
```scss
@font-face {
  font-family: "MyFont";
  src: url("../fonts/MyFont.woff2") format("woff2");
  font-weight: 400;
  font-style: normal;
  font-display: swap;
}

/* Apply globally */
.o_web_client, body {
  font-family: "MyFont", system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
}

/* Monospace for API code examples */
.api-code, .code-block, pre code {
  font-family: "SF Mono", "Monaco", "Inconsolata", "Fira Code", "Droid Sans Mono", "Source Code Pro", monospace;
}
```

Place the actual font file at:
`addons/ui_overrides/static/src/fonts/MyFont.woff2`

## Colors and variables

Many Odoo versions expose CSS variables for brand colors. If available, set them to override theme colors globally:
```scss
:root {
  /* Example variable names; adjust to your version */
  /* --o-brand-primary: #5b7fff; */
  /* --o-brand-contrast: #ffffff; */
  
  /* API-specific color variables */
  --api-primary: #2563eb;
  --api-success: #16a34a;
  --api-warning: #d97706;
  --api-error: #dc2626;
  --api-code-bg: #f8fafc;
  --api-code-border: #e2e8f0;
}
```

Fallback direct overrides (when variables are not present or for fine-grained tweaks):
```scss
a, .btn-primary {
  color: #ffffff;
  background-color: #5b7fff;
  border-color: #5b7fff;
}

.btn-secondary {
  color: #2f3542;
  background-color: #e9ecef;
  border-color: #e9ecef;
}
```

## API Documentation Styling (NEW)

Create specialized styling for API documentation interfaces:

`addons/ui_overrides/static/src/scss/api_docs.scss`
```scss
/* API Documentation Styling */
.api-docs-container {
  max-width: 1200px;
  margin: 0 auto;
  padding: 2rem;
  
  .api-endpoint {
    background: white;
    border: 1px solid var(--api-code-border);
    border-radius: 8px;
    margin-bottom: 2rem;
    overflow: hidden;
    
    .endpoint-header {
      background: var(--api-code-bg);
      padding: 1rem;
      border-bottom: 1px solid var(--api-code-border);
      
      .method {
        display: inline-block;
        padding: 0.25rem 0.5rem;
        border-radius: 4px;
        font-weight: bold;
        font-size: 0.75rem;
        text-transform: uppercase;
        
        &.get { background: var(--api-success); color: white; }
        &.post { background: var(--api-primary); color: white; }
        &.put { background: var(--api-warning); color: white; }
        &.delete { background: var(--api-error); color: white; }
      }
      
      .endpoint-url {
        font-family: monospace;
        font-size: 1.1rem;
        margin-left: 1rem;
        color: #374151;
      }
    }
    
    .endpoint-content {
      padding: 1.5rem;
      
      .description {
        margin-bottom: 1rem;
        color: #6b7280;
      }
      
      .parameters, .response-example {
        margin-bottom: 1.5rem;
        
        h4 {
          margin-bottom: 0.5rem;
          font-weight: 600;
          color: #374151;
        }
        
        .param {
          background: #f9fafb;
          border: 1px solid #e5e7eb;
          border-radius: 4px;
          padding: 0.75rem;
          margin-bottom: 0.5rem;
          
          .param-name {
            font-family: monospace;
            font-weight: bold;
            color: var(--api-primary);
          }
          
          .param-type {
            font-style: italic;
            color: #6b7280;
            margin-left: 0.5rem;
          }
          
          .param-description {
            margin-top: 0.25rem;
            color: #374151;
          }
        }
      }
      
      .code-example {
        background: #1f2937;
        color: #f9fafb;
        padding: 1rem;
        border-radius: 6px;
        overflow-x: auto;
        font-family: monospace;
        font-size: 0.875rem;
        
        .comment { color: #9ca3af; }
        .string { color: #10b981; }
        .number { color: #f59e0b; }
        .keyword { color: #3b82f6; }
      }
    }
  }
  
  .try-it-section {
    background: #f8fafc;
    border: 1px solid #e2e8f0;
    border-radius: 8px;
    padding: 1.5rem;
    margin-top: 1rem;
    
    .try-button {
      background: var(--api-primary);
      color: white;
      border: none;
      padding: 0.75rem 1.5rem;
      border-radius: 6px;
      cursor: pointer;
      font-weight: 500;
      
      &:hover {
        background: #1d4ed8;
      }
    }
    
    .response-display {
      background: #1f2937;
      color: #f9fafb;
      padding: 1rem;
      border-radius: 6px;
      margin-top: 1rem;
      font-family: monospace;
      font-size: 0.875rem;
      white-space: pre-wrap;
    }
  }
  
  .auth-section {
    background: #fef3c7;
    border: 1px solid #f59e0b;
    border-radius: 8px;
    padding: 1rem;
    margin-bottom: 2rem;
    
    .auth-title {
      font-weight: bold;
      color: #92400e;
      margin-bottom: 0.5rem;
    }
    
    .auth-input {
      width: 100%;
      padding: 0.5rem;
      border: 1px solid #d97706;
      border-radius: 4px;
      font-family: monospace;
    }
  }
  
  .status-indicator {
    display: inline-block;
    padding: 0.25rem 0.5rem;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: bold;
    
    &.production-ready {
      background: #dcfce7;
      color: #166534;
      border: 1px solid #16a34a;
    }
    
    &.beta {
      background: #fef3c7;
      color: #92400e;
      border: 1px solid #f59e0b;
    }
    
    &.deprecated {
      background: #fee2e2;
      color: #991b1b;
      border: 1px solid #dc2626;
    }
  }
}

/* API Explorer Widget */
.api-explorer {
  background: white;
  border: 2px solid #e5e7eb;
  border-radius: 12px;
  padding: 2rem;
  margin: 2rem 0;
  box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
  
  .explorer-header {
    text-align: center;
    margin-bottom: 2rem;
    
    h2 {
      color: var(--api-primary);
      margin-bottom: 0.5rem;
    }
    
    .subtitle {
      color: #6b7280;
    }
  }
  
  .endpoint-selector {
    margin-bottom: 1.5rem;
    
    select {
      width: 100%;
      padding: 0.75rem;
      border: 1px solid #d1d5db;
      border-radius: 6px;
      font-size: 1rem;
    }
  }
  
  .test-results {
    margin-top: 1.5rem;
    
    .result-success {
      background: #dcfce7;
      border: 1px solid #16a34a;
      color: #166534;
      padding: 1rem;
      border-radius: 6px;
    }
    
    .result-error {
      background: #fee2e2;
      border: 1px solid #dc2626;
      color: #991b1b;
      padding: 1rem;
      border-radius: 6px;
    }
  }
}

/* Code syntax highlighting for API examples */
.hljs {
  background: #1f2937 !important;
  color: #f9fafb !important;
  
  .hljs-string { color: #10b981; }
  .hljs-number { color: #f59e0b; }
  .hljs-keyword { color: #3b82f6; }
  .hljs-comment { color: #9ca3af; }
  .hljs-attr { color: #8b5cf6; }
  .hljs-built_in { color: #06b6d4; }
}

/* Responsive design for API docs */
@media (max-width: 768px) {
  .api-docs-container {
    padding: 1rem;
    
    .api-endpoint .endpoint-header {
      .method {
        display: block;
        margin-bottom: 0.5rem;
      }
      
      .endpoint-url {
        margin-left: 0;
        word-break: break-all;
      }
    }
    
    .code-example {
      font-size: 0.75rem;
    }
  }
}
```

## Website (frontend) overrides

If you are styling the public website:
- Depend on `website` or your theme module in `__manifest__.py`
- Target `web.assets_frontend` instead of `web.assets_backend`
- Put SCSS and fonts in the same `static/src/` structure

Example manifest change:
```python
{
  "depends": ["website"],
  "assets": {
    "web.assets_frontend": [
      "ui_overrides/static/src/scss/overrides.scss",
      "ui_overrides/static/src/scss/api_docs.scss",  # API documentation styling
    ],
  },
}
```

## API Documentation Templates (NEW)

Create QWeb templates for API documentation:

`addons/ui_overrides/static/src/templates/api_docs.xml`
```xml
<?xml version="1.0" encoding="utf-8"?>
<templates>
    <t t-name="api_docs.endpoint_template">
        <div class="api-endpoint">
            <div class="endpoint-header">
                <span t-attf-class="method #{method.lower()}" t-esc="method"/>
                <span class="endpoint-url" t-esc="url"/>
                <span t-if="status" t-attf-class="status-indicator #{status}" t-esc="status_text"/>
            </div>
            <div class="endpoint-content">
                <div class="description" t-esc="description"/>
                
                <div t-if="parameters" class="parameters">
                    <h4>Parameters</h4>
                    <t t-foreach="parameters" t-as="param">
                        <div class="param">
                            <span class="param-name" t-esc="param.name"/>
                            <span class="param-type" t-esc="param.type"/>
                            <div class="param-description" t-esc="param.description"/>
                        </div>
                    </t>
                </div>
                
                <div t-if="example_response" class="response-example">
                    <h4>Example Response</h4>
                    <div class="code-example" t-esc="example_response"/>
                </div>
                
                <div class="try-it-section">
                    <button class="try-button" t-att-data-endpoint="url">Try it out</button>
                    <div class="response-display" style="display: none;"></div>
                </div>
            </div>
        </div>
    </t>
    
    <t t-name="api_docs.explorer_widget">
        <div class="api-explorer">
            <div class="explorer-header">
                <h2>API Explorer</h2>
                <p class="subtitle">Test your API endpoints interactively</p>
            </div>
            
            <div class="auth-section">
                <div class="auth-title">API Authentication</div>
                <input type="text" class="auth-input" placeholder="Enter your API key" id="api-key-input"/>
            </div>
            
            <div class="endpoint-selector">
                <select id="endpoint-select">
                    <option value="">Select an endpoint to test...</option>
                    <option value="/api/v2/test">GET /api/v2/test - Basic test</option>
                    <option value="/api/v2/partners">GET /api/v2/partners - List partners</option>
                    <option value="/api/v2/products">GET /api/v2/products - List products</option>
                </select>
            </div>
            
            <button class="try-button" id="test-endpoint">Test Endpoint</button>
            
            <div class="test-results" id="test-results" style="display: none;"></div>
        </div>
    </t>
</templates>
```

## Interactive API Testing Widget (NEW)

Add JavaScript for API testing functionality:

`addons/ui_overrides/static/src/js/api_explorer.js`
```javascript
/** @odoo-module **/

import { Component, useState } from "@odoo/owl";

export class ApiExplorer extends Component {
    setup() {
        this.state = useState({
            apiKey: '',
            selectedEndpoint: '',
            testing: false,
            result: null,
            error: null
        });
    }
    
    async testEndpoint() {
        if (!this.state.selectedEndpoint) {
            this.state.error = "Please select an endpoint";
            return;
        }
        
        this.state.testing = true;
        this.state.error = null;
        this.state.result = null;
        
        try {
            const headers = {
                'Content-Type': 'application/json'
            };
            
            if (this.state.apiKey) {
                headers['api-key'] = this.state.apiKey;
            }
            
            const response = await fetch(this.state.selectedEndpoint, {
                method: 'GET',
                headers: headers
            });
            
            const data = await response.json();
            
            if (response.ok) {
                this.state.result = {
                    status: response.status,
                    data: JSON.stringify(data, null, 2)
                };
            } else {
                this.state.error = `Error ${response.status}: ${data.error?.message || 'Unknown error'}`;
            }
        } catch (error) {
            this.state.error = `Network error: ${error.message}`;
        } finally {
            this.state.testing = false;
        }
    }
    
    onEndpointChange(event) {
        this.state.selectedEndpoint = event.target.value;
    }
    
    onApiKeyChange(event) {
        this.state.apiKey = event.target.value;
    }
}

ApiExplorer.template = "ui_overrides.api_explorer_template";
```

## Ensure your styles take precedence

- Assets are applied in the order they are listed in bundles. Appending your SCSS to the bundle ensures it loads after defaults.
- Use selectors with equal or higher specificity. Avoid `!important` unless necessary.
- **For API documentation: Use specific class names to avoid conflicts with main UI**

## Apply and iterate

Update and reload during development:
```bash
python3 odoo-bin -c odoo.conf -d <db> -u ui_overrides --dev=assets,qweb
```

Tips:
- Use `--dev=assets` to reduce asset caching during development
- Use browser dev tools to inspect applied rules and origins
- **Test API documentation styling across different screen sizes**

## Common targets

### Standard UI Elements
- Global font: `.o_web_client, body { font-family: ... }`
- Buttons: `.btn`, `.btn-primary`, `.btn-secondary`
- Form view labels/inputs: `.o_form_label`, `.o_input` (version-specific)
- Navbar/menus (backend): `.o_main_navbar`, `.o_menu_sections`
- Kanban cards: `.o_kanban_record`
- Website components: target your theme's classes or structural elements as needed

### API-Specific Elements (NEW)
- API documentation: `.api-docs-container`, `.api-endpoint`
- Code examples: `.code-example`, `.hljs`
- API explorer: `.api-explorer`, `.try-button`
- Status indicators: `.status-indicator`
- Method badges: `.method.get`, `.method.post`, etc.
- Response displays: `.response-display`, `.test-results`

## API Documentation Best Practices

### Color Coding
```scss
// HTTP method color coding
.method {
  &.get { background: #10b981; }      // Green for safe operations
  &.post { background: #3b82f6; }     // Blue for creation
  &.put { background: #f59e0b; }      // Orange for updates  
  &.patch { background: #8b5cf6; }    // Purple for partial updates
  &.delete { background: #ef4444; }   // Red for deletion
}

// Status indicators
.status-indicator {
  &.stable { background: #dcfce7; color: #166534; }
  &.beta { background: #fef3c7; color: #92400e; }
  &.deprecated { background: #fee2e2; color: #991b1b; }
  &.experimental { background: #f3e8ff; color: #7c3aed; }
}
```

### Typography for Code
```scss
.api-code {
  font-family: "SF Mono", "Monaco", "Inconsolata", "Fira Code", monospace;
  font-size: 0.875rem;
  line-height: 1.5;
  
  // Syntax highlighting
  .keyword { color: #3b82f6; font-weight: bold; }
  .string { color: #10b981; }
  .number { color: #f59e0b; }
  .comment { color: #9ca3af; font-style: italic; }
  .url { color: #06b6d4; text-decoration: underline; }
}
```

## Don't forget

- Keep overrides in your own module; avoid editing core addons
- Separate backend and website changes by targeting the appropriate bundle
- **Include API documentation styling in your UI theme**
- **Test interactive API widgets across browsers**
- Commit font files with proper licensing
- **Ensure API documentation is accessible and mobile-friendly**

## Integration with base_api Module

When styling API documentation that integrates with the base_api module:

1. **Coordinate with API endpoints** - Ensure styling supports all base_api endpoints
2. **Match authentication patterns** - Style should reflect the dual auth system (API keys + sessions)
3. **Include test functionality** - Interactive widgets should work with actual base_api endpoints
4. **Status indicators** - Reflect the production-ready status of base_api endpoints
5. **Error handling** - Style error responses consistently with base_api error format

This ensures a cohesive experience between your API functionality and documentation presentation.
