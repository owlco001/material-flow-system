"""管理员运维 CLI：部署期管理员设定与密码重置（docs/DEPLOYMENT.md「Administrator provisioning」）。

用法（在部署目录执行）：
    .venv/bin/python -m app.manage_admin create --employee-no <工号> --name <显示名> [--role ADMIN]
    .venv/bin/python -m app.manage_admin reset-password --employee-no <工号>
    .venv/bin/python -m app.manage_admin list
    .venv/bin/python -m app.manage_admin status

安全约定：密码只经交互式 getpass（不回显、二次确认）或 --password-stdin 读取，
绝不接受命令行参数，避免落入 shell 历史 / 进程列表 / 日志。create / reset 都强制
首次登录改密（must_change_password=1），与 INITIAL_ADMIN_PASSWORD 机制同语义。
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys
import uuid

from app import main as backend

MIN_PASSWORD_LENGTH = 8
MANAGEABLE_ROLES = ("ADMIN", "WAREHOUSE_ADMIN", "PLANNER", "WORKSHOP_SUPERVISOR")


def read_password(*, from_stdin: bool = False, confirm: bool = True) -> str:
    """读取新密码：stdin 一行，或 getpass 双次确认；短于 8 位直接拒绝。"""
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("新密码（输入不回显）: ")
        if confirm:
            again = getpass.getpass("再次输入确认: ")
            if password != again:
                raise ValueError("两次输入不一致")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    return password


def create_admin(employee_no: str, display_name: str, role: str, password: str) -> dict:
    """创建管理侧账号（bootstrap 场景：库内可能没有任何用户，无法走 API 的 actor 语义）。"""
    employee_no = employee_no.strip()
    display_name = display_name.strip()
    if not employee_no:
        raise ValueError("工号不能为空")
    if not display_name:
        raise ValueError("显示名不能为空")
    if role not in MANAGEABLE_ROLES:
        raise ValueError(f"role 只能是 {'/'.join(MANAGEABLE_ROLES)}")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    c = backend.db()
    try:
        if c.execute("SELECT 1 FROM users WHERE username=?", (employee_no,)).fetchone():
            raise ValueError("工号已存在")
        user_id = f"u_{uuid.uuid4().hex[:12]}"
        c.execute(
            "INSERT INTO users(id,username,display_name,role,password_hash,"
            "must_change_password,active,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (user_id, employee_no, display_name, role, backend.hash_password(password), 1, 1, backend.now()),
        )
        c.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
            "request_id,client_operation_id,before_json,after_json,server_time,device_id,source_ip,result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ADMIN_PROVISIONED_CLI", "USER", user_id, user_id, "CLI", str(uuid.uuid4()),
             str(uuid.uuid4()), "{}", json.dumps({"username": employee_no, "role": role}), backend.now(),
             None, None, "SUCCESS"),
        )
        c.commit()
        return {"userId": user_id, "username": employee_no, "role": role, "mustChangePassword": True}
    finally:
        c.close()


def reset_password(employee_no: str, password: str) -> dict:
    """重置密码并吊销该用户全部 APP/WEB 会话（与 API reset_employee_password 同 SQL 语义）。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"密码至少 {MIN_PASSWORD_LENGTH} 位")
    c = backend.db()
    try:
        target = c.execute("SELECT * FROM users WHERE username=?", (employee_no.strip(),)).fetchone()
        if not target:
            raise ValueError("工号不存在")
        c.execute(
            "UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
            (backend.hash_password(password), target["id"]),
        )
        app_revoked = c.execute("DELETE FROM sessions WHERE user_id=?", (target["id"],))
        web_revoked = c.execute("DELETE FROM web_sessions WHERE user_id=?", (target["id"],))
        c.execute(
            "INSERT INTO audit_events(event_type,entity_type,entity_id,actor_user_id,actor_role,"
            "request_id,client_operation_id,before_json,after_json,server_time,device_id,source_ip,result)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("ADMIN_PASSWORD_RESET_CLI", "USER", target["id"], target["id"], "CLI", str(uuid.uuid4()),
             str(uuid.uuid4()), "{}", json.dumps({"revokedSessions": app_revoked.rowcount,
                                                  "revokedWebSessions": web_revoked.rowcount}),
             backend.now(), None, None, "SUCCESS"),
        )
        c.commit()
        return {
            "userId": target["id"], "username": target["username"],
            "revokedSessions": app_revoked.rowcount, "revokedWebSessions": web_revoked.rowcount,
            "mustChangePassword": True,
        }
    finally:
        c.close()


def list_users() -> list[dict]:
    c = backend.db()
    try:
        rows = c.execute(
            "SELECT username, display_name, role, active, must_change_password FROM users ORDER BY created_at"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        c.close()


def setup_status() -> dict:
    c = backend.db()
    try:
        row = c.execute("SELECT * FROM setup_state WHERE id='default'").fetchone()
        return dict(row) if row else {"state": "UNINITIALIZED"}
    finally:
        c.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.manage_admin", description=__doc__)
    parser.add_argument("--password-stdin", action="store_true", help="从 stdin 读一行作为密码（自动化场景）")
    sub = parser.add_subparsers(dest="command", required=True)
    p_create = sub.add_parser("create", help="创建管理侧账号（强制首登改密）")
    p_create.add_argument("--employee-no", required=True)
    p_create.add_argument("--name", required=True)
    p_create.add_argument("--role", default="ADMIN", choices=MANAGEABLE_ROLES)
    p_reset = sub.add_parser("reset-password", help="重置密码并吊销该用户全部会话")
    p_reset.add_argument("--employee-no", required=True)
    sub.add_parser("list", help="列出全部账号")
    sub.add_parser("status", help="查看初始化状态")
    args = parser.parse_args(argv)

    try:
        if args.command == "create":
            result = create_admin(
                args.employee_no, args.name, args.role,
                read_password(from_stdin=args.password_stdin),
            )
        elif args.command == "reset-password":
            result = reset_password(args.employee_no, read_password(from_stdin=args.password_stdin))
        elif args.command == "list":
            result = list_users()
        else:
            result = setup_status()
    except ValueError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
