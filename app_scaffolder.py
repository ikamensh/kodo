"""AppScaffolder — Generate new project structures from specifications."""

import json
import os
from pathlib import Path
from typing import Dict, Optional

from kodo.requirements_parser import Spec


class AppScaffolder:
    """Generate project directory structures and initial files from specs."""

    def __init__(self, base_path: Optional[Path] = None):
        """
        Initialize scaffolder.

        Args:
            base_path: Base path for scaffolding (defaults to current directory)
        """
        self.base_path = Path(base_path) if base_path else Path.cwd()

    def scaffold(self, spec: Spec, output_dir: Optional[str] = None) -> Path:
        """
        Scaffold a new project from a specification.

        Args:
            spec: Specification describing the project
            output_dir: Optional output directory name (defaults to spec.project_name)

        Returns:
            Path to the scaffolded project directory
        """
        project_dir = output_dir or spec.project_name.replace(" ", "-").lower()
        project_path = self.base_path / project_dir

        # Create root directory
        project_path.mkdir(parents=True, exist_ok=True)

        # Create directory structure
        self._create_directory_structure(project_path, spec)

        # Create configuration files
        self._create_package_json(project_path, spec)
        self._create_gitignore(project_path, spec)
        self._create_env_example(project_path, spec)
        self._create_readme(project_path, spec)
        self._create_tsconfig(project_path, spec)
        self._create_docker_files(project_path, spec)

        # Create backend structure
        if spec.backend_framework:
            self._create_backend_structure(project_path, spec)

        # Create frontend structure
        if spec.frontend_framework:
            self._create_frontend_structure(project_path, spec)

        # Create spec file for reference
        self._create_spec_file(project_path, spec)

        return project_path

    def _create_directory_structure(self, project_path: Path, spec: Spec) -> None:
        """Create the main directory structure."""
        dirs = [
            "src",
            "tests",
            ".github/workflows",
            "docs",
            ".vscode",
        ]

        if spec.backend_framework:
            dirs.extend([
                "src/api",
                "src/middleware",
                "src/utils",
                "src/models",
                "src/services",
            ])

        if spec.database:
            dirs.extend([
                "src/database",
                "migrations",
            ])

        if spec.frontend_framework:
            dirs.extend([
                "src/components",
                "src/pages",
                "src/styles",
                "src/hooks",
                "src/store",
            ])

        for dir_name in dirs:
            (project_path / dir_name).mkdir(parents=True, exist_ok=True)
            # Create .gitkeep to ensure dirs are tracked
            (project_path / dir_name / ".gitkeep").touch()

    def _create_package_json(self, project_path: Path, spec: Spec) -> None:
        """Create package.json with appropriate dependencies."""
        package = {
            "name": spec.project_name.replace(" ", "-").lower(),
            "version": "0.1.0",
            "description": spec.description,
            "type": "module",
            "main": "src/index.js",
            "scripts": {
                "dev": "node --watch src/index.js",
                "build": "tsc",
                "test": "jest",
                "lint": "eslint src/",
                "format": "prettier --write src/",
            },
            "dependencies": self._get_dependencies(spec),
            "devDependencies": self._get_dev_dependencies(spec),
            "keywords": [f.name.lower() for f in spec.features],
            "author": "",
            "license": "MIT",
        }

        package_json_path = project_path / "package.json"
        package_json_path.write_text(json.dumps(package, indent=2) + "\n")

    def _get_dependencies(self, spec: Spec) -> Dict[str, str]:
        """Get main dependencies based on spec."""
        deps = {}

        if spec.backend_framework == "express":
            deps.update({
                "express": "^4.18.0",
                "dotenv": "^16.0.0",
                "cors": "^2.8.5",
            })

            if spec.auth and spec.auth.auth_type == "jwt":
                deps["jsonwebtoken"] = "^9.0.0"

            if spec.database:
                if spec.database.orm_type == "prisma":
                    deps["@prisma/client"] = "latest"
                elif spec.database.orm_type == "mongoose":
                    deps["mongoose"] = "^7.0.0"

        if spec.frontend_framework == "react":
            deps.update({
                "react": "^18.0.0",
                "react-dom": "^18.0.0",
            })

        if spec.database and spec.database.orm_type == "mongoose":
            deps["mongoose"] = "^7.0.0"

        return deps

    def _get_dev_dependencies(self, spec: Spec) -> Dict[str, str]:
        """Get dev dependencies based on spec."""
        deps = {
            "typescript": "^5.0.0",
            "jest": "^29.0.0",
            "@types/node": "^18.0.0",
            "eslint": "^8.0.0",
            "prettier": "^3.0.0",
        }

        if spec.backend_framework == "express":
            deps["@types/express"] = "^4.17.0"

        return deps

    def _create_gitignore(self, project_path: Path, spec: Spec) -> None:
        """Create .gitignore file."""
        gitignore_content = """\
# Dependencies
node_modules/
.pnp
.pnp.js
package-lock.json
yarn.lock

# Testing
coverage/
.pytest_cache/
__pycache__/
*.pyc

# Production
dist/
build/
.next/
out/

# Environment variables
.env
.env.local
.env.*.local

# Editor
.vscode/
.idea/
*.swp
*.swo
*~
.DS_Store

# Logs
logs/
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*

# Database
*.sqlite
*.sqlite3
.prisma/
prisma/migrations/

# Temporary files
tmp/
temp/
"""
        (project_path / ".gitignore").write_text(gitignore_content)

    def _create_env_example(self, project_path: Path, spec: Spec) -> None:
        """Create .env.example file."""
        env_vars = [
            "# Environment",
            "NODE_ENV=development",
        ]

        if spec.auth:
            env_vars.extend([
                "",
                "# Authentication",
                "JWT_SECRET=your-secret-key-change-in-production",
            ])
            if spec.auth.providers:
                if "google" in spec.auth.providers:
                    env_vars.append("GOOGLE_CLIENT_ID=")
                    env_vars.append("GOOGLE_CLIENT_SECRET=")

        if spec.database:
            env_vars.extend([
                "",
                "# Database",
                f"DATABASE_URL=",
            ])

        if any("stripe" in f.name.lower() for f in spec.features):
            env_vars.extend([
                "",
                "# Payments",
                "STRIPE_SECRET_KEY=",
                "STRIPE_PUBLISHABLE_KEY=",
            ])

        if any("email" in f.name.lower() for f in spec.features):
            env_vars.extend([
                "",
                "# Email",
                "SMTP_HOST=",
                "SMTP_PORT=",
                "SMTP_USER=",
                "SMTP_PASS=",
            ])

        (project_path / ".env.example").write_text("\n".join(env_vars) + "\n")

    def _create_readme(self, project_path: Path, spec: Spec) -> None:
        """Create README.md file."""
        tech_stack = ", ".join([t.choice for t in spec.tech_stack])
        features = "\n".join([f"- {f.name}: {f.description}" for f in spec.features])

        readme_content = f"""\
# {spec.project_name}

{spec.description}

## Tech Stack

{tech_stack}

## Features

{features}

## Getting Started

### Prerequisites
- Node.js 18+
- npm or yarn

### Installation

```bash
npm install
```

### Development

```bash
npm run dev
```

### Testing

```bash
npm test
```

### Building

```bash
npm run build
```

## Deployment

See [DEPLOYMENT.md](./docs/DEPLOYMENT.md) for deployment instructions.

## Project Structure

```
{spec.project_name}/
├── src/
│   ├── api/           # API endpoints
│   ├── middleware/    # Express middleware
│   ├── models/        # Data models
│   ├── services/      # Business logic
│   └── utils/         # Utility functions
├── tests/             # Test files
├── migrations/        # Database migrations
├── docs/              # Documentation
└── .env.example       # Environment variables example
```

## Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](./CONTRIBUTING.md) for details on our code of conduct and the process for submitting pull requests.

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Support

For support, email support@{spec.project_name.lower().replace(" ", "-")}.dev or open an issue on GitHub.
"""
        (project_path / "README.md").write_text(readme_content)

    def _create_tsconfig(self, project_path: Path, spec: Spec) -> None:
        """Create tsconfig.json file."""
        tsconfig = {
            "compilerOptions": {
                "target": "ES2020",
                "module": "ESNext",
                "lib": ["ES2020"],
                "outDir": "./dist",
                "rootDir": "./src",
                "strict": True,
                "esModuleInterop": True,
                "skipLibCheck": True,
                "forceConsistentCasingInFileNames": True,
                "resolveJsonModule": True,
                "declaration": True,
                "declarationMap": True,
                "sourceMap": True,
            },
            "include": ["src/**/*"],
            "exclude": ["node_modules", "dist", "tests"],
        }

        (project_path / "tsconfig.json").write_text(json.dumps(tsconfig, indent=2) + "\n")

    def _create_docker_files(self, project_path: Path, spec: Spec) -> None:
        """Create Docker configuration files."""
        # Dockerfile
        if spec.backend_framework == "express":
            dockerfile_content = """\
FROM node:18-alpine

WORKDIR /app

# Copy package files
COPY package*.json ./

# Install dependencies
RUN npm ci --only=production

# Copy source code
COPY src ./src
COPY tsconfig.json ./

# Build TypeScript
RUN npm run build

# Expose port
EXPOSE 3000

# Start application
CMD ["node", "dist/index.js"]
"""
            (project_path / "Dockerfile").write_text(dockerfile_content)

        # docker-compose.yml if database is needed
        if spec.database:
            services = {"app": {"build": ".", "ports": ["3000:3000"]}}

            if spec.database.db_type == "postgresql":
                services["postgres"] = {
                    "image": "postgres:15-alpine",
                    "environment": {
                        "POSTGRES_PASSWORD": "postgres",
                        "POSTGRES_DB": spec.project_name.replace(" ", "_").lower(),
                    },
                    "ports": ["5432:5432"],
                    "volumes": ["postgres_data:/var/lib/postgresql/data"],
                }
            elif spec.database.db_type == "mongodb":
                services["mongodb"] = {
                    "image": "mongo:6",
                    "ports": ["27017:27017"],
                    "volumes": ["mongo_data:/data/db"],
                }

            docker_compose = {
                "version": "3.8",
                "services": services,
                "volumes": {},
            }

            if spec.database.db_type == "postgresql":
                docker_compose["volumes"]["postgres_data"] = None
            elif spec.database.db_type == "mongodb":
                docker_compose["volumes"]["mongo_data"] = None

            (project_path / "docker-compose.yml").write_text(
                self._dict_to_yaml(docker_compose)
            )

    def _create_backend_structure(self, project_path: Path, spec: Spec) -> None:
        """Create backend-specific files."""
        if spec.backend_framework == "express":
            # Create main index file
            index_content = """\
import express from 'express';
import cors from 'cors';
import dotenv from 'dotenv';

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3000;

// Middleware
app.use(cors());
app.use(express.json());

// Health check
app.get('/health', (req, res) => {
  res.json({ status: 'ok' });
});

// Start server
app.listen(PORT, () => {
  console.log(`Server is running on port ${PORT}`);
});

export default app;
"""
            (project_path / "src" / "index.ts").write_text(index_content)

            # Create sample API route
            api_content = """\
import { Router } from 'express';

const router = Router();

// TODO: Add your API routes here
router.get('/', (req, res) => {
  res.json({ message: 'Welcome to the API' });
});

export default router;
"""
            (project_path / "src" / "api" / "routes.ts").write_text(api_content)

    def _create_frontend_structure(self, project_path: Path, spec: Spec) -> None:
        """Create frontend-specific files."""
        if spec.frontend_framework == "react":
            # Create main App component
            app_content = f"""\
import React from 'react';
import './App.css';

function App() {{
  return (
    <div className="App">
      <header>
        <h1>Welcome to {spec.project_name}</h1>
        <p>Building amazing things...</p>
      </header>
    </div>
  );
}}

export default App;
"""
            (project_path / "src" / "components" / "App.tsx").write_text(app_content)

            # Create index page
            index_content = """\
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './components/App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
"""
            (project_path / "src" / "index.tsx").write_text(index_content)

    def _create_spec_file(self, project_path: Path, spec: Spec) -> None:
        """Create .kodo/spec.json file with the original specification."""
        kodo_dir = project_path / ".kodo"
        kodo_dir.mkdir(exist_ok=True)

        spec_file = kodo_dir / "spec.json"
        spec_file.write_text(spec.to_json())

    def _dict_to_yaml(self, data: Dict, indent: int = 0) -> str:
        """Simple dict to YAML converter (minimal implementation)."""
        result = []
        indent_str = "  " * indent

        for key, value in data.items():
            if isinstance(value, dict):
                result.append(f"{indent_str}{key}:")
                result.append(self._dict_to_yaml(value, indent + 1))
            elif isinstance(value, list):
                result.append(f"{indent_str}{key}:")
                for item in value:
                    if isinstance(item, dict):
                        result.append(self._dict_to_yaml(item, indent + 1))
                    else:
                        result.append(f"{indent_str}  - {item}")
            elif isinstance(value, bool):
                result.append(f"{indent_str}{key}: {str(value).lower()}")
            elif value is None:
                result.append(f"{indent_str}{key}:")
            else:
                result.append(f'{indent_str}{key}: "{value}"')

        return "\n".join(result)


def scaffold_project(spec: Spec, output_dir: Optional[str] = None) -> Path:
    """Convenience function to scaffold a project."""
    scaffolder = AppScaffolder()
    return scaffolder.scaffold(spec, output_dir)
