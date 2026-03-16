"""Benchmark API server: authenticated uploads + data serving from Firestore/GCS.

Write endpoints (require Bearer token from Firestore token registry):
    POST /api/task-result   — upload one task result + patch
    POST /api/run           — register a new benchmark run
    POST /api/eval-results  — upload evaluation results

Admin endpoints (require admin token from KODO_BENCH_ADMIN_TOKEN env var):
    POST   /admin/tokens    — create a new token {name, issued_to, notes}
    GET    /admin/tokens    — list all tokens with metadata
    DELETE /admin/tokens/{id_or_prefix} — revoke a token

Read endpoints (authenticated):
    GET /api/unevaluated/{dataset}    — predictions needing evaluation (with patches)

Read endpoints (public):
    GET /data/{dataset}/index.json    — aggregated results (from Firestore, cached)
    GET /data/{dataset}/patches.json  — all patches (from GCS, cached)
    GET /api/patch/{dataset}/{instance_id}/{arm}  — single patch from GCS
    GET /                             — viewer SPA

Deploy: gcloud run deploy kodo-bench --source benchmark/online --allow-unauthenticated
"""

from __future__ import annotations

import http.server
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("benchmark.online")

from . import db
from .config import ADMIN_TOKEN
from .validation import suspicious_upload_reason

PORT = int(os.environ.get("PORT", 8080))
BASE_PATH = os.environ.get("BASE_PATH", "")  # e.g. "/bench" when behind Firebase Hosting
STATIC_DIR = Path(__file__).parent / "static"

# In-memory cache: key -> (timestamp, bytes)
_cache: dict[str, tuple[float, bytes]] = {}
CACHE_TTL = 300  # 5 minutes — index.json is materialized in GCS, this is just edge cache


class Handler(http.server.BaseHTTPRequestHandler):
    # ── Routing ───────────────────────────────────────────────────────

    def _strip_base(self) -> str:
        """Strip BASE_PATH prefix from self.path for routing."""
        if BASE_PATH and self.path.startswith(BASE_PATH):
            stripped = self.path[len(BASE_PATH):]
            return stripped or "/"
        return self.path

    def do_GET(self):
        p = self._strip_base()
        if p.startswith("/data/"):
            self._serve_data(p)
        elif p.startswith("/api/unevaluated/"):
            if self._check_api_token():
                self._handle_unevaluated(p)
        elif p.startswith("/api/scheduling/"):
            self._handle_scheduling(p)
        elif p.startswith("/api/patch/"):
            self._serve_patch(p)
        elif p == "/admin/tokens":
            self._handle_list_tokens()
        elif p == "/api/whoami":
            if self._check_api_token():
                self._handle_whoami()
        elif p == "/api/health":
            self._json_ok({"status": "ok"})
        else:
            self._serve_static(p)

    def do_POST(self):
        p = self._strip_base()
        if p == "/admin/tokens":
            self._handle_create_token()
        elif p == "/api/task-result":
            if self._check_api_token():
                self._handle_task_result()
        elif p == "/api/run":
            if self._check_api_token():
                self._handle_run()
        elif p == "/api/eval-results":
            if self._check_api_token():
                self._handle_eval_results()
        elif p == "/api/next-tasks":
            if self._check_api_token():
                self._handle_next_tasks()
        elif p == "/api/register":
            self._handle_register()
        else:
            self.send_error(404)

    def do_DELETE(self):
        p = self._strip_base()
        if p.startswith("/admin/tokens/"):
            self._handle_revoke_token()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    # ── Auth ──────────────────────────────────────────────────────────

    def _get_bearer_token(self) -> str:
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return ""

    def _check_admin(self) -> bool:
        """Verify the request carries the admin token."""
        token = self._get_bearer_token()
        if not ADMIN_TOKEN or token != ADMIN_TOKEN:
            self.send_error(401, "Invalid or missing admin token")
            return False
        return True

    def _check_api_token(self) -> bool:
        """Verify the request carries a valid API token from Firestore."""
        token = self._get_bearer_token()
        if not token:
            self.send_error(401, "Missing Authorization header")
            return False
        try:
            meta = db.validate_token(token)
        except Exception as e:
            self.send_error(502, f"Token validation error: {e}")
            return False
        if meta is None:
            self.send_error(401, "Invalid or revoked API token")
            return False
        return True

    # ── Token management (admin) ──────────────────────────────────────

    def _handle_create_token(self):
        if not self._check_admin():
            return
        body = self._read_json()
        if body is None:
            return
        name = body.get("name", "")
        issued_to = body.get("issued_to", "")
        if not name or not issued_to:
            self.send_error(400, "Missing 'name' and/or 'issued_to'")
            return
        try:
            token = db.create_token(
                name=name,
                issued_to=issued_to,
                notes=body.get("notes", ""),
            )
        except Exception as e:
            self.send_error(500, str(e))
            return
        self._json_ok({"token": token, "note": "Save this token — it cannot be retrieved again."})

    def _handle_list_tokens(self):
        if not self._check_admin():
            return
        try:
            tokens = db.list_tokens()
        except Exception as e:
            self.send_error(502, str(e))
            return
        self._json_ok({"tokens": tokens})

    def _handle_revoke_token(self):
        if not self._check_admin():
            return
        # /admin/tokens/{id_or_prefix}
        token_id = self._strip_base().split("/admin/tokens/", 1)[-1]
        if not token_id:
            self.send_error(400, "Missing token ID or prefix")
            return
        try:
            found = db.revoke_token(token_id)
        except Exception as e:
            self.send_error(500, str(e))
            return
        if found:
            self._json_ok({"revoked": True})
        else:
            self.send_error(404, "Token not found")

    # ── Write handlers ────────────────────────────────────────────────

    def _handle_task_result(self):
        body = self._read_json()
        if body is None:
            return

        dataset = body.get("dataset", "")
        iid = body.get("instance_id", "")
        arm = body.get("arm", "")
        if not dataset or not iid or not arm:
            self.send_error(400, "Missing dataset, instance_id, or arm")
            return

        result_data = {
            "status": body.get("status", ""),
            "elapsed_s": body.get("elapsed_s", 0),
            "patch_len": body.get("patch_len", 0),
            "error": body.get("error", ""),
            "run_id": body.get("run_id", ""),
            "provenance": body.get("provenance", {}),
        }
        reason = suspicious_upload_reason(
            arm=arm,
            status=result_data["status"],
            elapsed_s=result_data["elapsed_s"],
            patch=body.get("patch", ""),
            patch_len=result_data["patch_len"],
            error=result_data["error"],
            agent_output=body.get("agent_output"),
        )
        if reason:
            self._json_ok({"ok": True, "skipped": True, "reason": reason})
            return

        try:
            db.save_task_result(dataset, iid, arm, result_data)
            patch = body.get("patch", "")
            if patch:
                db.save_patch(dataset, iid, arm, patch)
        except Exception as e:
            self.send_error(500, str(e))
            return

        # Release any claim on this (instance_id, arm) pair
        try:
            db.release_claim(dataset, iid, arm)
        except Exception:
            pass  # best-effort

        # Implicit activity signal: contributor is actively producing results for this arm
        prov = result_data.get("provenance") or {}
        contributor = f"{prov.get('user', '')}@{prov.get('host', '')}" if prov.get("user") else ""
        if contributor:
            try:
                db.touch_activity(dataset, contributor, [arm])
            except Exception:
                pass  # best-effort

        # db.save_task_result already sets dirty flag — materialization happens on next read
        _cache.pop(f"index:{dataset}", None)

        self._json_ok({"ok": True})

    def _handle_run(self):
        body = self._read_json()
        if body is None:
            return
        run_id = body.pop("run_id", "")
        if not run_id:
            self.send_error(400, "Missing run_id")
            return
        try:
            db.save_run(run_id, body)
        except Exception as e:
            self.send_error(500, str(e))
            return
        self._json_ok({"ok": True})

    def _handle_eval_results(self):
        body = self._read_json()
        if body is None:
            return
        dataset = body.get("dataset", "")
        arm = body.get("arm", "")
        if not dataset or not arm:
            self.send_error(400, "Missing dataset or arm")
            return
        try:
            db.save_eval_results(
                dataset,
                arm,
                resolved=body.get("resolved", []),
                failed=body.get("failed", []),
                error=body.get("error", []),
            )
        except Exception as e:
            self.send_error(500, str(e))
            return
        _cache.pop(f"index:{dataset}", None)
        self._json_ok({"ok": True})

    def _handle_next_tasks(self):
        """Distribute tasks: return prioritized assignments for a contributor."""
        body = self._read_json()
        if body is None:
            return

        backends = body.get("backends", [])
        datasets = body.get("datasets", {})
        if not backends or not datasets:
            self.send_error(400, "Missing backends or datasets")
            return

        limit = body.get("limit", 20)
        contributor = body.get("contributor", "unknown")
        ttl_seconds = body.get("ttl_seconds", db.CLAIM_TTL_SECONDS)

        try:
            all_assignments: list[dict] = []
            for ds_key, instance_ids in datasets.items():
                if not instance_ids:
                    continue
                remaining = limit - len(all_assignments)
                if remaining <= 0:
                    break
                assignments = db.get_next_tasks(
                    dataset=ds_key,
                    instance_ids=instance_ids,
                    backends=backends,
                    contributor=contributor,
                    limit=remaining,
                    ttl_seconds=ttl_seconds,
                )
                for a in assignments:
                    a["dataset"] = ds_key
                all_assignments.extend(assignments)
        except Exception as e:
            log.exception("Error in next-tasks: %s", e)
            self.send_error(500, str(e))
            return

        self._json_ok({"assignments": all_assignments})

    # ── Token identity ─────────────────────────────────────────────

    def _handle_whoami(self):
        token = self._get_bearer_token()
        try:
            meta = db.validate_token(token)
        except Exception:
            meta = None
        if not meta:
            self.send_error(401)
            return
        self._json_ok({
            "name": meta.get("name", ""),
            "issued_to": meta.get("issued_to", ""),
        })

    # ── Self-service registration ───────────────────────────────────

    def _handle_register(self):
        body = self._read_json()
        if body is None:
            return
        name = (body.get("name") or "").strip()
        agreed = body.get("agreed", False)
        if not name:
            self.send_error(400, "Missing name")
            return
        if not agreed:
            self.send_error(400, "Must agree to the benchmark guidelines")
            return
        try:
            token = db.create_token(
                name=name,
                issued_to=name,
                notes="self-registered",
            )
        except Exception as e:
            self.send_error(500, str(e))
            return
        self._json_ok({"token": token})

    # ── Read handlers ─────────────────────────────────────────────────

    def _handle_scheduling(self, p: str = ""):
        """Serve GET /api/scheduling/{dataset} — live scheduling state."""
        m = re.match(r"/api/scheduling/(\w+)$", p or self._strip_base())
        if not m:
            self.send_error(404)
            return
        dataset = m.group(1)
        try:
            info = db.get_scheduling_info(dataset)
        except Exception as e:
            self.send_error(502, f"Backend error: {e}")
            return
        self._json_ok(info)

    def _handle_unevaluated(self, p: str = ""):
        """Serve GET /api/unevaluated/{dataset} — predictions needing evaluation."""
        m = re.match(r"/api/unevaluated/(\w+)$", p or self._strip_base())
        if not m:
            self.send_error(404)
            return
        dataset = m.group(1)
        try:
            predictions = db.get_unevaluated(dataset)
        except Exception as e:
            self.send_error(502, f"Backend error: {e}")
            return
        self._json_ok({"dataset": dataset, "predictions": predictions})

    def _serve_data(self, p: str = ""):
        """Serve /data/{dataset}/index.json or patches.json from Firestore/GCS."""
        m = re.match(r"/data/(\w+)/(index|patches)\.json", p or self._strip_base())
        if not m:
            self.send_error(404)
            return
        dataset, kind = m.group(1), m.group(2)

        cache_key = f"{kind}:{dataset}"
        now = time.time()
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < CACHE_TTL:
            self._raw_response(200, cached[1], "application/json")
            return

        try:
            if kind == "index":
                # Materialized index.json in GCS — cheap read, lazy rebuild on dirty/stale
                body = db.get_index_json(dataset)
            else:
                data = db.get_all_patches(dataset)
                body = json.dumps(data).encode()
        except Exception as e:
            self.send_error(502, f"Backend error: {e}")
            return

        _cache[cache_key] = (now, body)
        self._raw_response(200, body, "application/json")

    def _serve_patch(self, p: str = ""):
        """Serve /api/patch/{dataset}/{instance_id}/{arm}."""
        # instance_id contains "__" so we parse carefully
        m = re.match(r"/api/patch/(\w+)/(.+)/([^/]+)$", p or self._strip_base())
        if not m:
            self.send_error(404)
            return
        dataset, iid, arm = m.group(1), m.group(2), m.group(3)

        try:
            patch = db.get_patch(dataset, iid, arm)
        except Exception as e:
            self.send_error(502, str(e))
            return

        if patch is None:
            self.send_error(404, "Patch not found")
            return

        self._raw_response(200, patch.encode(), "text/plain")

    def _serve_static(self, p: str = ""):
        path = (p or self._strip_base()).rstrip("/")
        if path in ("", "/index.html"):
            fpath = STATIC_DIR / "index.html"
        else:
            fpath = STATIC_DIR / path.lstrip("/")

        if not fpath.is_file():
            self.send_error(404)
            return

        content = fpath.read_bytes()
        ctype = {
            ".html": "text/html",
            ".js": "application/javascript",
            ".css": "text/css",
            ".json": "application/json",
        }.get(fpath.suffix, "application/octet-stream")

        self._raw_response(200, content, ctype)

    # ── Helpers ───────────────────────────────────────────────────────

    def _read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            return json.loads(body)
        except (json.JSONDecodeError, ValueError) as e:
            self.send_error(400, f"Invalid JSON: {e}")
            return None

    def _json_ok(self, data: dict):
        self._raw_response(200, json.dumps(data).encode(), "application/json")

    def _raw_response(self, code: int, body: bytes, content_type: str):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")

    def log_message(self, fmt, *args):
        pass  # Silence request logs


if __name__ == "__main__":
    server = http.server.HTTPServer(("", PORT), Handler)
    print(f"Benchmark API server on :{PORT}")
    print(f"  Project: {os.environ.get('KODO_BENCH_PROJECT', '(default)')}")
    print(f"  Bucket:  {os.environ.get('KODO_BENCH_BUCKET', '(default)')}")
    print(f"  Admin token: {'set' if ADMIN_TOKEN else 'NOT SET (admin endpoints disabled)'}")
    server.serve_forever()
