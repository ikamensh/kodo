"""DatabaseSchemaGenerator — Auto-generate database schemas and migrations."""

import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from kodo.requirements_parser import Feature, Spec


@dataclass
class ColumnDefinition:
    """Single column/field definition."""
    name: str
    type: str  # "string", "integer", "float", "boolean", "datetime", "json"
    required: bool = True
    unique: bool = False
    indexed: bool = False
    default: Optional[str] = None


@dataclass
class TableDefinition:
    """Single table/model definition."""
    name: str
    description: str
    columns: List[ColumnDefinition]
    primary_key: str = "id"
    timestamps: bool = True  # created_at, updated_at


class DatabaseSchemaGenerator:
    """Generate database schemas and migrations from specifications."""

    def __init__(self, db_type: str = "postgresql"):
        """
        Initialize schema generator.

        Args:
            db_type: Database type ("postgresql", "mongodb", "mysql", "sqlite")
        """
        self.db_type = db_type

    def generate_schema_from_spec(self, spec: Spec) -> List[TableDefinition]:
        """
        Generate database schema from specification.

        Args:
            spec: Project specification

        Returns:
            List of table definitions
        """
        tables = []

        # Always create users table if auth is enabled
        if spec.auth:
            tables.append(self._create_users_table())

        # Create tables for each feature
        for feature in spec.features:
            table = self._create_table_from_feature(feature)
            if table:
                tables.append(table)

        return tables

    def _create_users_table(self) -> TableDefinition:
        """Create default users table."""
        return TableDefinition(
            name="users",
            description="User accounts and authentication",
            columns=[
                ColumnDefinition("id", "string", required=True, unique=True, indexed=True),
                ColumnDefinition("email", "string", required=True, unique=True, indexed=True),
                ColumnDefinition("password_hash", "string", required=True),
                ColumnDefinition("first_name", "string", required=False),
                ColumnDefinition("last_name", "string", required=False),
                ColumnDefinition("is_active", "boolean", default="true"),
                ColumnDefinition("last_login", "datetime", required=False),
            ],
        )

    def _create_table_from_feature(self, feature: Feature) -> Optional[TableDefinition]:
        """Create a table definition from a feature."""
        if not feature.requires_api:
            return None

        # Convert feature name to table name
        table_name = feature.name.lower().replace(" ", "_")

        # Create basic columns
        columns = [
            ColumnDefinition("id", "string", required=True, unique=True, indexed=True),
            ColumnDefinition("name", "string", required=True, indexed=True),
            ColumnDefinition("description", "string", required=False),
        ]

        # Add feature-specific columns
        if "user" in table_name.lower():
            columns.extend([
                ColumnDefinition("email", "string", required=False, unique=True),
                ColumnDefinition("role", "string", default="user"),
            ])

        if "product" in table_name.lower():
            columns.extend([
                ColumnDefinition("price", "float", required=True),
                ColumnDefinition("sku", "string", unique=True),
                ColumnDefinition("in_stock", "boolean", default="true"),
            ])

        if "order" in table_name.lower():
            columns.extend([
                ColumnDefinition("user_id", "string", required=True, indexed=True),
                ColumnDefinition("total_amount", "float", required=True),
                ColumnDefinition("status", "string", default="pending"),
            ])

        if "payment" in table_name.lower():
            columns.extend([
                ColumnDefinition("amount", "float", required=True),
                ColumnDefinition("status", "string", default="pending"),
                ColumnDefinition("transaction_id", "string", unique=True),
            ])

        return TableDefinition(
            name=table_name,
            description=feature.description,
            columns=columns,
        )

    def generate_sql(self, tables: List[TableDefinition]) -> str:
        """
        Generate SQL DDL for tables.

        Args:
            tables: List of table definitions

        Returns:
            SQL DDL as string
        """
        if self.db_type == "postgresql":
            return self._generate_postgresql(tables)
        elif self.db_type == "mysql":
            return self._generate_mysql(tables)
        elif self.db_type == "sqlite":
            return self._generate_sqlite(tables)
        else:
            raise ValueError(f"Unsupported database type: {self.db_type}")

    def _generate_postgresql(self, tables: List[TableDefinition]) -> str:
        """Generate PostgreSQL DDL."""
        sql = "-- PostgreSQL Schema\n"
        sql += f"-- Generated: {datetime.now().isoformat()}\n\n"

        for table in tables:
            sql += f"-- {table.description}\n"
            sql += f"CREATE TABLE IF NOT EXISTS {table.name} (\n"

            # Add columns
            column_defs = []
            for col in table.columns:
                col_sql = f"  {col.name} {self._get_postgres_type(col.type)}"

                if col.required:
                    col_sql += " NOT NULL"

                if col.unique:
                    col_sql += " UNIQUE"

                if col.default:
                    col_sql += f" DEFAULT {col.default}"

                if col.name == table.primary_key:
                    col_sql += " PRIMARY KEY"

                column_defs.append(col_sql)

            # Add timestamps
            if table.timestamps:
                column_defs.append(
                    "  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
                column_defs.append(
                    "  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )

            sql += ",\n".join(column_defs)
            sql += "\n);\n\n"

            # Add indexes
            for col in table.columns:
                if col.indexed and col.name != table.primary_key:
                    sql += f"CREATE INDEX idx_{table.name}_{col.name} ON {table.name}({col.name});\n"

        return sql

    def _generate_mysql(self, tables: List[TableDefinition]) -> str:
        """Generate MySQL DDL."""
        sql = "-- MySQL Schema\n"
        sql += f"-- Generated: {datetime.now().isoformat()}\n\n"

        for table in tables:
            sql += f"-- {table.description}\n"
            sql += f"CREATE TABLE IF NOT EXISTS `{table.name}` (\n"

            column_defs = []
            for col in table.columns:
                col_sql = f"  `{col.name}` {self._get_mysql_type(col.type)}"

                if col.required:
                    col_sql += " NOT NULL"

                if col.unique:
                    col_sql += " UNIQUE"

                if col.default:
                    col_sql += f" DEFAULT {col.default}"

                if col.name == table.primary_key:
                    col_sql += " PRIMARY KEY AUTO_INCREMENT"

                column_defs.append(col_sql)

            if table.timestamps:
                column_defs.append(
                    "  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
                column_defs.append(
                    "  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"
                )

            sql += ",\n".join(column_defs)
            sql += "\n);\n\n"

        return sql

    def _generate_sqlite(self, tables: List[TableDefinition]) -> str:
        """Generate SQLite DDL."""
        sql = "-- SQLite Schema\n"
        sql += f"-- Generated: {datetime.now().isoformat()}\n\n"

        for table in tables:
            sql += f"-- {table.description}\n"
            sql += f"CREATE TABLE IF NOT EXISTS {table.name} (\n"

            column_defs = []
            for col in table.columns:
                col_sql = f"  {col.name} {self._get_sqlite_type(col.type)}"

                if col.required:
                    col_sql += " NOT NULL"

                if col.unique:
                    col_sql += " UNIQUE"

                if col.default:
                    col_sql += f" DEFAULT {col.default}"

                if col.name == table.primary_key:
                    col_sql += " PRIMARY KEY"

                column_defs.append(col_sql)

            if table.timestamps:
                column_defs.append("  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                column_defs.append("  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")

            sql += ",\n".join(column_defs)
            sql += "\n);\n\n"

        return sql

    def generate_migration_file(
        self, tables: List[TableDefinition], output_path: Path
    ) -> str:
        """
        Generate a migration file.

        Args:
            tables: List of table definitions
            output_path: Path where migration file will be written

        Returns:
            Generated migration file content
        """
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        migration_name = f"{timestamp}_initial_schema.sql"

        # Create output directory if it doesn't exist
        output_path.mkdir(parents=True, exist_ok=True)

        sql = self.generate_sql(tables)
        migration_path = output_path / migration_name
        migration_path.write_text(sql)

        return str(migration_path)

    def generate_prisma_schema(self, tables: List[TableDefinition]) -> str:
        """
        Generate Prisma schema.

        Args:
            tables: List of table definitions

        Returns:
            Prisma schema as string
        """
        schema = """generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

"""

        for table in tables:
            schema += f"model {self._to_pascal_case(table.name)} {{\n"

            for col in table.columns:
                prisma_type = self._get_prisma_type(col.type)
                col_def = f"  {col.name} {prisma_type}"

                if col.name == table.primary_key:
                    col_def += " @id @default(cuid())"
                elif col.indexed and col.unique:
                    col_def += " @unique"
                elif col.indexed:
                    col_def += " @db.Indexed()"

                schema += col_def + "\n"

            if table.timestamps:
                schema += "  createdAt DateTime @default(now())\n"
                schema += "  updatedAt DateTime @updatedAt\n"

            schema += "}\n\n"

        return schema

    def generate_mongodb_schema(self, tables: List[TableDefinition]) -> Dict:
        """
        Generate MongoDB collection schema.

        Args:
            tables: List of table definitions

        Returns:
            MongoDB validation schema as dict
        """
        schemas = {}

        for table in tables:
            properties = {}

            for col in table.columns:
                mongo_type = self._get_mongodb_type(col.type)
                prop = {"bsonType": mongo_type}

                if col.required:
                    prop["description"] = f"{col.name} (required)"

                properties[col.name] = prop

            if table.timestamps:
                properties["createdAt"] = {"bsonType": "date"}
                properties["updatedAt"] = {"bsonType": "date"}

            schemas[table.name] = {
                "validator": {
                    "$jsonSchema": {
                        "bsonType": "object",
                        "required": [
                            col.name for col in table.columns if col.required
                        ],
                        "properties": properties,
                    }
                }
            }

        return schemas

    def _get_postgres_type(self, col_type: str) -> str:
        """Map Python type to PostgreSQL type."""
        mapping = {
            "string": "VARCHAR(255)",
            "integer": "INTEGER",
            "float": "DECIMAL(10,2)",
            "boolean": "BOOLEAN",
            "datetime": "TIMESTAMP",
            "json": "JSONB",
        }
        return mapping.get(col_type, "VARCHAR(255)")

    def _get_mysql_type(self, col_type: str) -> str:
        """Map Python type to MySQL type."""
        mapping = {
            "string": "VARCHAR(255)",
            "integer": "INT",
            "float": "DECIMAL(10,2)",
            "boolean": "BOOLEAN",
            "datetime": "DATETIME",
            "json": "JSON",
        }
        return mapping.get(col_type, "VARCHAR(255)")

    def _get_sqlite_type(self, col_type: str) -> str:
        """Map Python type to SQLite type."""
        mapping = {
            "string": "TEXT",
            "integer": "INTEGER",
            "float": "REAL",
            "boolean": "INTEGER",
            "datetime": "DATETIME",
            "json": "TEXT",
        }
        return mapping.get(col_type, "TEXT")

    def _get_prisma_type(self, col_type: str) -> str:
        """Map Python type to Prisma type."""
        mapping = {
            "string": "String",
            "integer": "Int",
            "float": "Float",
            "boolean": "Boolean",
            "datetime": "DateTime",
            "json": "Json",
        }
        return mapping.get(col_type, "String")

    def _get_mongodb_type(self, col_type: str) -> str:
        """Map Python type to MongoDB type."""
        mapping = {
            "string": "string",
            "integer": "int",
            "float": "double",
            "boolean": "bool",
            "datetime": "date",
            "json": "object",
        }
        return mapping.get(col_type, "string")

    def _to_pascal_case(self, snake_str: str) -> str:
        """Convert snake_case to PascalCase."""
        return "".join(word.title() for word in snake_str.split("_"))


def generate_database_schema(spec: Spec, output_dir: Path, db_type: str = "postgresql") -> None:
    """
    Convenience function to generate database schema.

    Args:
        spec: Project specification
        output_dir: Output directory for generated files
        db_type: Database type
    """
    if not spec.database:
        return

    generator = DatabaseSchemaGenerator(spec.database.db_type)
    tables = generator.generate_schema_from_spec(spec)

    # Generate SQL migration for SQL databases
    if spec.database.db_type != "mongodb":
        generator.generate_migration_file(tables, output_dir / "migrations")

    # Generate Prisma schema if ORM is Prisma
    if spec.database.orm_type == "prisma":
        prisma_schema = generator.generate_prisma_schema(tables)
        (output_dir / "prisma" / "schema.prisma").parent.mkdir(parents=True, exist_ok=True)
        (output_dir / "prisma" / "schema.prisma").write_text(prisma_schema)

    # Generate MongoDB schema if using MongoDB
    if spec.database.db_type == "mongodb":
        mongo_schemas = generator.generate_mongodb_schema(tables)
        (output_dir / "schemas").mkdir(parents=True, exist_ok=True)
        (output_dir / "schemas" / "mongodb.json").write_text(json.dumps(mongo_schemas, indent=2))
