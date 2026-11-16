"""AWS CLI のコマンドカタログ。

AWS CLI 自体が使っている botocore のサービス定義から、全サービス・全コマンド・
全オプションを自動生成します。botocore を更新すれば新サービスも自動で増えます。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from html import unescape
from pathlib import Path

import botocore
import botocore.session
from botocore import xform_name

from . import ja

DOC_BASE = "https://docs.aws.amazon.com/cli/latest/reference"

# botocore 名 → AWS CLI 上のサービス名
SERVICE_RENAMES = {"s3": "s3api", "config": "configservice", "codedeploy": "deploy"}
SERVICE_RENAMES_REV = {v: k for k, v in SERVICE_RENAMES.items()}

ARG_RENAMES = json.loads((Path(__file__).parent / "aws_arg_renames.json").read_text())

GLOBAL_OPTIONS = [
    {"name": "--region", "type": "string", "doc": "The Region to use (e.g. ap-northeast-1).",
     "suggest": ["ap-northeast-1", "ap-northeast-3", "us-east-1", "us-west-2", "eu-west-1"]},
    {"name": "--profile", "type": "string", "doc": "Use a specific profile from your credential file (~/.aws/config)."},
    {"name": "--output", "type": "enum", "doc": "The formatting style for command output.",
     "choices": ["json", "text", "table", "yaml", "yaml-stream"]},
    {"name": "--query", "type": "string", "doc": "A JMESPath query to use in filtering the response data (e.g. Reservations[].Instances[].InstanceId)."},
    {"name": "--no-cli-pager", "type": "flag", "doc": "Disable cli pager for output."},
    {"name": "--endpoint-url", "type": "string", "doc": "Override command's default URL with the given URL."},
    {"name": "--debug", "type": "flag", "doc": "Turn on debug logging."},
    {"name": "--cli-read-timeout", "type": "number", "doc": "The maximum socket read time in seconds."},
]

PAGINATION_OPTIONS = [
    {"name": "--max-items", "type": "number", "doc": "The total number of items to return in the command's output (the CLI paginates automatically)."},
    {"name": "--page-size", "type": "number", "doc": "The size of each page to get in the AWS service call."},
    {"name": "--starting-token", "type": "string", "doc": "A token to specify where to start paginating (the NextToken from a previously truncated response)."},
    {"name": "--no-paginate", "type": "flag", "doc": "Disable automatic pagination (only the first page is returned)."},
]

# CLI 独自（botocoreにない）よく使うコマンド。説明（doc）は英語。日本語は ja_docs.json / ja.AWS_COMMAND_JA で引く
P = lambda name, doc, type_="string", required=False, **kw: {  # noqa: E731
    "name": name, "type": type_, "required": required, "doc": doc, **kw}
A = lambda name, doc, required=True: {"name": name, "doc": doc, "required": required}  # noqa: E731
DRYRUN = P("--dryrun", "Displays the operations that would be performed without actually running them.", "flag")
EXCLUDE = P("--exclude", "Exclude all files or objects that match the specified pattern (e.g. \"*.log\").")
INCLUDE = P("--include", "Don't exclude files or objects that match the specified pattern.")
CUSTOM_COMMANDS = {
    "s3": {
        "ls": {"summary": "List S3 buckets, or objects and common prefixes under a prefix.",
               "positionals": [A("path", "S3 path (e.g. s3://my-bucket/prefix/). Omit to list all buckets.", False)],
               "params": [P("--recursive", "List all objects under the prefix recursively.", "flag"),
                          P("--human-readable", "Display file sizes in human readable format.", "flag"),
                          P("--summarize", "Display summary information (number of objects, total size).", "flag")]},
        "cp": {"summary": "Copy a local file or S3 object to another location locally or in S3.",
               "positionals": [A("source", "Copy source (local path or s3://...)."), A("dest", "Copy destination (local path or s3://...).")],
               "params": [P("--recursive", "Copy all files or objects under the specified directory or prefix.", "flag"), EXCLUDE, INCLUDE, DRYRUN,
                          P("--storage-class", "The type of storage to use for the object.", "enum", choices=["STANDARD", "REDUCED_REDUNDANCY", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING", "GLACIER", "DEEP_ARCHIVE", "GLACIER_IR"]),
                          P("--sse", "Specifies server-side encryption of the object in S3.", "enum", choices=["AES256", "aws:kms", "aws:kms:dsse"]),
                          P("--acl", "Sets the ACL for the object when the command is performed.", "enum", choices=["private", "public-read", "bucket-owner-full-control"]),
                          P("--quiet", "Does not display the operations performed from the specified command.", "flag"),
                          P("--content-type", "Specify an explicit content type for this operation.")]},
        "mv": {"summary": "Move a local file or S3 object to another location (copy, then delete the source).",
               "positionals": [A("source", "Move source."), A("dest", "Move destination.")],
               "params": [P("--recursive", "Move all files or objects under the specified directory or prefix.", "flag"), EXCLUDE, INCLUDE, DRYRUN]},
        "rm": {"summary": "Delete an S3 object.",
               "positionals": [A("path", "The object to delete (s3://...).")],
               "params": [P("--recursive", "Delete all objects under the specified prefix.", "flag"), EXCLUDE, INCLUDE, DRYRUN]},
        "sync": {"summary": "Sync directories and S3 prefixes.",
                 "positionals": [A("source", "Sync source."), A("dest", "Sync destination.")],
                 "params": [P("--delete", "Files that exist in the destination but not in the source are deleted during sync.", "flag"), EXCLUDE, INCLUDE, DRYRUN,
                            P("--size-only", "Makes the size of each key the only criteria used to decide whether to sync.", "flag"),
                            P("--exact-timestamps", "Compare timestamps exactly when syncing.", "flag"),
                            P("--storage-class", "The type of storage to use for the object.", "enum", choices=["STANDARD", "STANDARD_IA", "ONEZONE_IA", "INTELLIGENT_TIERING", "GLACIER", "DEEP_ARCHIVE", "GLACIER_IR"])]},
        "mb": {"summary": "Create an S3 bucket.", "positionals": [A("bucket", "The bucket to create (s3://bucket-name).")], "params": []},
        "rb": {"summary": "Delete an S3 bucket.", "positionals": [A("bucket", "The bucket to delete (s3://bucket-name).")],
               "params": [P("--force", "Delete all objects in the bucket, then delete the bucket.", "flag")]},
        "presign": {"summary": "Generate a pre-signed URL for an S3 object.",
                    "positionals": [A("path", "The target object (s3://bucket/key).")],
                    "params": [P("--expires-in", "Number of seconds until the pre-signed URL expires (default 3600).", "number")]},
        "website": {"summary": "Set the static website configuration for a bucket.",
                    "positionals": [A("bucket", "The target bucket (s3://bucket-name).")],
                    "params": [P("--index-document", "The index document (e.g. index.html)."), P("--error-document", "The error document.")]},
    },
    "logs": {
        "tail": {"summary": "Show logs of a log group, and optionally follow new events.",
                 "positionals": [A("group_name", "The name of the log group.")],
                 "params": [P("--follow", "Keep waiting for and displaying new logs.", "flag"),
                            P("--since", "From what time to begin displaying logs (e.g. 10m, 1h, 2d)."),
                            P("--filter-pattern", "The filter pattern to use."),
                            P("--format", "The format of the logs.", "enum", choices=["detailed", "short", "json"]),
                            P("--log-stream-name-prefix", "Prefix of the log stream names.")]},
    },
    "cloudformation": {
        "deploy": {"summary": "Deploy a CloudFormation template (create or update a stack via a change set).", "positionals": [],
                   "params": [P("--template-file", "The path to the template file.", required=True),
                              P("--stack-name", "The name of the stack.", required=True),
                              P("--parameter-overrides", "Parameter values (Key=Value, separated by spaces).", "list"),
                              P("--capabilities", "Capabilities to allow (e.g. CAPABILITY_NAMED_IAM).", "list"),
                              P("--no-execute-changeset", "Only create the change set; do not execute it.", "flag"),
                              P("--no-fail-on-empty-changeset", "Do not fail when there are no changes to the stack.", "flag"),
                              P("--s3-bucket", "The S3 bucket to upload the template to."),
                              P("--tags", "Tags (Key=Value).", "list")]},
        "package": {"summary": "Package local artifacts referenced by a template and upload them to S3.", "positionals": [],
                    "params": [P("--template-file", "The path to the template file.", required=True),
                               P("--s3-bucket", "The S3 bucket to upload artifacts to.", required=True),
                               P("--output-template-file", "The path to write the output template."),
                               P("--s3-prefix", "The S3 prefix.")]},
    },
    "ecr": {
        "get-login-password": {"summary": "Get a password to log in to ECR (pipe it to docker login).", "positionals": [], "params": []},
    },
    "eks": {
        "update-kubeconfig": {"summary": "Update the kubeconfig for kubectl.", "positionals": [],
                              "params": [P("--name", "The name of the cluster.", required=True),
                                         P("--alias", "Alias for the cluster context name."),
                                         P("--role-arn", "The ARN of the role to use for authentication."),
                                         P("--kubeconfig", "The path to the kubeconfig file.")]},
    },
    "configure": {
        "list": {"summary": "List the current configuration (profile, region, etc.).", "positionals": [], "params": []},
        "get": {"summary": "Get a configuration value.", "positionals": [A("varname", "The name of the setting (e.g. region).")], "params": []},
        "set": {"summary": "Set a configuration value.",
                "positionals": [A("varname", "The name of the setting (e.g. region)."), A("value", "The value.")], "params": []},
        "list-profiles": {"summary": "List all profile names.", "positionals": [], "params": []},
        "sso": {"summary": "Configure an IAM Identity Center (SSO) profile interactively.", "positionals": [], "params": []},
    },
    "sso": {
        "login": {"summary": "Log in with SSO (browser authentication).", "positionals": [], "params": []},
        "logout": {"summary": "Log out of SSO.", "positionals": [], "params": []},
    },
}
EXTRA_POSITIONALS = {("lambda", "invoke"): [A("outfile", "The file to save the response to (e.g. out.json).")]}

_session = botocore.session.get_session()


def strip_html(text: str | None, limit: int | None = None) -> str:
    if not text:
        return ""
    t = re.sub(r"<[^>]+>", " ", text)
    t = unescape(re.sub(r"\s+", " ", t)).strip()
    if limit and len(t) > limit:
        t = t[:limit].rstrip() + "…"
    return t


def first_sentence(text: str) -> str:
    m = re.match(r"(.+?[.。])(\s|$)", text)
    return (m.group(1) if m else text)[:220]


def cli_service_name(botocore_name: str) -> str:
    return SERVICE_RENAMES.get(botocore_name, botocore_name)


def service_doc_url(svc: str) -> str:
    return f"{DOC_BASE}/{svc}/index.html"


def command_doc_url(svc: str, cmd: str) -> str:
    return f"{DOC_BASE}/{svc}/{cmd}.html"


def split_words(kebab: str) -> list[str]:
    return kebab.split("-")


def ja_summary(svc: str, cmd: str) -> str:
    key = f"{svc} {cmd}"
    if key in ja.AWS_COMMAND_JA:
        return ja.AWS_COMMAND_JA[key]
    words = split_words(cmd)
    verb = words[0]
    if verb in ja.AWS_VERB_JA:
        noun = "-".join(words[1:]) or svc
        return f"{noun} {ja.AWS_VERB_JA[verb]}"
    return ""


@lru_cache(maxsize=None)
def _rank(name: str) -> int:
    keys = list(ja.AWS_SERVICE_JA)
    return keys.index(name) if name in keys else 9999


@lru_cache(maxsize=None)
def _model(botocore_name: str):
    return _session.get_service_model(botocore_name)


# ---------- wait コマンド（botocore の waiter 定義から自動生成。例: aws ec2 wait instance-running） ----------
@lru_cache(maxsize=None)
def _waiters(botocore_name: str) -> dict:
    try:
        wm = _session.get_waiter_model(botocore_name)
    except Exception:  # noqa: BLE001  waiter のないサービス
        return {}
    return {xform_name(n, "-"): wm.get_waiter(n) for n in wm.waiter_names}


def _waiter_doc(w) -> str:
    """AWS CLI リファレンスと同じ形の説明文（ja_docs のパターンで日本語化できる）"""
    op = xform_name(w.operation, "-")
    acc = next((a for a in w.acceptors if a.state == "success"), None)
    val = lambda v: str(v).lower() if isinstance(v, bool) else str(v)  # noqa: E731
    if acc is None:
        cond = f"Wait until the expected state is reached when polling with {op}."
    elif acc.matcher in ("path", "pathAll", "pathAny"):
        scope = {"pathAll": " for all elements", "pathAny": " for any element"}.get(acc.matcher, "")
        cond = f"Wait until JMESPath query {acc.argument} returns {val(acc.expected)}{scope} when polling with {op}."
    elif acc.matcher == "status":
        cond = f"Wait until {acc.expected} response is received when polling with {op}."
    else:
        cond = f"Wait until {acc.expected} is thrown when polling with {op}."
    return (f"{cond} It will poll every {w.delay} seconds until a successful state has been reached. "
            f"This will exit with a return code of 255 after {w.max_attempts} failed checks.")


@lru_cache(maxsize=1)
def services() -> list[dict]:
    out = []
    names = set()
    for b in _session.get_available_services():
        m = _model(b)
        svc = cli_service_name(b)
        names.add(svc)
        ops = len(m.operation_names) + len(CUSTOM_COMMANDS.get(svc, {})) + len(_waiters(b))
        out.append({
            "name": svc,
            "title": m.metadata.get("serviceFullName", svc),
            "ja": ja.AWS_SERVICE_JA.get(svc, ""),
            "count": ops, "rank": _rank(svc),
            "doc_url": service_doc_url(svc),
        })
    for svc, cmds in CUSTOM_COMMANDS.items():
        if svc not in names:
            out.append({"name": svc, "title": {"s3": "Amazon S3 (high-level commands)",
                                               "configure": "AWS CLI configuration"}.get(svc, svc),
                        "ja": ja.AWS_SERVICE_JA.get(svc, {"configure": "CLI設定（認証情報・リージョン）"}.get(svc, "")),
                        "count": len(cmds), "rank": _rank(svc), "doc_url": service_doc_url(svc)})
    out.sort(key=lambda s: s["name"])
    return out


def _botocore_name(svc: str) -> str | None:
    if svc in SERVICE_RENAMES:  # 例: "s3" は CLI では高レベルコマンド（botocore の s3 は s3api）
        return None
    b = SERVICE_RENAMES_REV.get(svc, svc)
    return b if b in _session.get_available_services() else None


@lru_cache(maxsize=1024)   # サービスは約400。URL に任意の名前を入れられるので、覚える数に上限を付ける
def commands(svc: str) -> list[dict]:
    out = {}
    b = _botocore_name(svc)
    if b:
        m = _model(b)
        for op in m.operation_names:
            om = m.operation_model(op)
            if om.deprecated:
                continue
            cmd = xform_name(op, "-")
            req = len(om.input_shape.required_members) if om.input_shape else 0
            out[cmd] = {
                "name": cmd, "op": op,
                "summary": first_sentence(strip_html(om.documentation)),
                "ja": ja_summary(svc, cmd),
                "required": req,
                "doc_url": command_doc_url(svc, cmd),
            }
        for wname, w in _waiters(b).items():
            cmd = f"wait {wname}"
            out[cmd] = {"name": cmd, "op": w.operation, "summary": first_sentence(_waiter_doc(w)),
                        "ja": f"{wname} になるまで待機する",
                        "required": len(m.operation_model(w.operation).input_shape.required_members)
                        if m.operation_model(w.operation).input_shape else 0,
                        "doc_url": f"{DOC_BASE}/{svc}/wait/{wname}.html", "waiter": True}
    for cmd, spec in CUSTOM_COMMANDS.get(svc, {}).items():
        out[cmd] = {"name": cmd, "op": None, "summary": spec.get("summary") or "AWS CLI custom command",
                    "ja": ja_summary(svc, cmd),
                    "required": sum(1 for p in spec["positionals"] if p["required"]) + sum(1 for p in spec["params"] if p["required"]),
                    "doc_url": command_doc_url(svc, cmd), "custom": True}
    return sorted(out.values(), key=lambda c: c["name"])


@lru_cache(maxsize=1)
def search_index() -> list[dict]:
    idx = []
    for s in services():
        for c in commands(s["name"]):
            idx.append({"service": s["name"], "service_ja": s["ja"], "name": c["name"],
                        "curated": f"{s['name']} {c['name']}" in ja.AWS_COMMAND_JA,
                        "ja": c["ja"], "summary": c["summary"], "doc_url": c["doc_url"]})
    return idx


# ---------- パラメータの型・サンプル ----------
SCALARS = {"string", "integer", "long", "float", "double", "boolean", "timestamp", "blob"}


def _placeholder(shape):
    if getattr(shape, "enum", None):
        return "|".join(shape.enum[:4]) + ("|…" if len(shape.enum) > 4 else "")
    return {"string": "string", "integer": 0, "long": 0, "float": 0.0, "double": 0.0,
            "boolean": True, "timestamp": "1970-01-01T00:00:00", "blob": "blob"}.get(shape.type_name, "string")


def skeleton(shape, depth=0):
    if depth > 4:
        return "..."
    t = shape.type_name
    if t == "structure":
        return {k: skeleton(v, depth + 1) for k, v in shape.members.items()}
    if t == "list":
        return [skeleton(shape.member, depth + 1)]
    if t == "map":
        return {"KeyName": skeleton(shape.value, depth + 1)}
    return _placeholder(shape)


def shorthand(shape) -> str | None:
    """AWS CLI の簡略構文の例（scalar か scalarのlist だけで構成される構造のみ）"""
    target = shape.member if shape.type_name == "list" else shape
    if target.type_name != "structure" or not target.members:
        return None
    parts = []
    for k, v in target.members.items():
        if v.type_name in SCALARS:
            parts.append(f"{k}={_placeholder(v)}")
        elif v.type_name == "list" and v.member.type_name in SCALARS:
            parts.append(f"{k}={_placeholder(v.member)},{_placeholder(v.member)}")
        else:
            return None
    return ",".join(parts)


def _rename(svc, cmd, arg):
    return ARG_RENAMES.get(f"{svc}.{cmd}.{arg}") or ARG_RENAMES.get(f"{svc}.*.{arg}") or arg


def _param(svc, cmd, member, shape, required):
    arg = xform_name(member, "-")
    t = shape.type_name
    p = {"key": member, "required": required, "doc": strip_html(shape.documentation, 600),
         "shape": shape.name}
    if t == "boolean":
        p.update(type="boolean", name="--" + _rename(svc, cmd, arg), neg="--" + _rename(svc, cmd, "no-" + arg))
    elif getattr(shape, "enum", None):
        p.update(type="enum", name="--" + _rename(svc, cmd, arg), choices=list(shape.enum))
    elif t in ("integer", "long", "float", "double"):
        p.update(type="number", name="--" + _rename(svc, cmd, arg))
    elif t == "list" and shape.member.type_name in SCALARS:
        p.update(type="list", name="--" + _rename(svc, cmd, arg),
                 choices=list(getattr(shape.member, "enum", None) or []) or None)
    elif t in ("structure", "map", "list"):
        p.update(type="json", name="--" + _rename(svc, cmd, arg),
                 example=json.dumps(skeleton(shape), ensure_ascii=False), shorthand=shorthand(shape))
    else:
        p.update(type="string", name="--" + _rename(svc, cmd, arg))
        if t == "blob":
            p["hint"] = "To pass a file: fileb://path"
        if t == "timestamp":
            p["hint"] = "e.g. 2026-01-01T00:00:00Z"
    return p


@lru_cache(maxsize=4096)
def command_detail(svc: str, cmd: str) -> dict | None:
    custom = CUSTOM_COMMANDS.get(svc, {}).get(cmd)
    base = {"provider": "aws", "service": svc, "name": cmd,
            "service_ja": ja.AWS_SERVICE_JA.get(svc, ""),
            "doc_url": command_doc_url(svc, cmd), "service_doc_url": service_doc_url(svc),
            "global_options": GLOBAL_OPTIONS, "ja": ja_summary(svc, cmd)}
    if custom:
        base.update(summary=custom.get("summary", ""), description=custom.get("summary", ""), custom=True,
                    positionals=custom["positionals"], params=custom["params"], pagination=[])
        return base
    b = _botocore_name(svc)
    if not b:
        return None
    m = _model(b)
    if cmd.startswith("wait "):
        return _waiter_detail(svc, cmd, base, m, _waiters(b).get(cmd[5:]))
    op = next((o for o in m.operation_names if xform_name(o, "-") == cmd), None)
    if not op:
        return None
    om = m.operation_model(op)
    hidden = set()
    pagination = []
    try:
        pcfg = _session.get_paginator_model(b)._paginator_config.get(op)
    except Exception:  # noqa: BLE001
        pcfg = None
    if pcfg:
        tok = pcfg.get("input_token")
        hidden.update(tok if isinstance(tok, list) else [tok])
        pagination = [o for o in PAGINATION_OPTIONS if o["name"] != "--page-size" or pcfg.get("limit_key")]
        if pcfg.get("limit_key"):
            hidden.add(pcfg["limit_key"])
    params = []
    if om.input_shape:
        req = set(om.input_shape.required_members)
        for name, shape in om.input_shape.members.items():
            if name in hidden:
                continue
            params.append(_param(svc, cmd, name, shape, name in req))
    params.sort(key=lambda p: (not p["required"], p["name"]))
    full = strip_html(om.documentation, 1200)
    base.update(op=op, summary=first_sentence(full), description=full,
                positionals=EXTRA_POSITIONALS.get((svc, cmd), []), params=params, pagination=pagination)
    return base


def _waiter_detail(svc, cmd, base, m, w):
    if w is None:
        return None
    om = m.operation_model(w.operation)
    op_cli = xform_name(w.operation, "-")
    params = []
    if om.input_shape:
        req = set(om.input_shape.required_members)
        params = [_param(svc, op_cli, n, s, n in req) for n, s in om.input_shape.members.items()]
    params.sort(key=lambda p: (not p["required"], p["name"]))
    doc = _waiter_doc(w)
    base.update(op=w.operation, summary=first_sentence(doc), description=doc, waiter=True,
                doc_url=f"{DOC_BASE}/{svc}/wait/{cmd[5:]}.html",
                positionals=[], params=params, pagination=[])
    return base


def warmup():
    try:
        search_index()
    except Exception as e:  # noqa: BLE001
        print("AWS index warmup failed:", e)


def botocore_version() -> str:
    return botocore.__version__
