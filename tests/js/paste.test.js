"use strict";
// 貼り付けたコマンドの読み取り（app/static/paste.js）。
const test = require("node:test");
const assert = require("node:assert/strict");
const P = require("../../app/static/paste.js");

// ---------- parse ----------
test("simple command", () => {
  assert.deepEqual(P.parse("aws ec2 describe-instances --instance-ids i-1 i-2"),
    { service: "ec2", command: "describe-instances", args: ["--instance-ids", "i-1", "i-2"], warnings: [] });
});

test("line continuations: bash / PowerShell / cmd", () => {
  for (const text of ["aws ec2 stop-instances \\\n  --instance-ids i-1 \\\n  --dry-run",
    "aws ec2 stop-instances `\n  --instance-ids i-1 `\n  --dry-run",
    "aws ec2 stop-instances ^\n  --instance-ids i-1 ^\n  --dry-run"]) {
    assert.deepEqual(P.parse(text).args, ["--instance-ids", "i-1", "--dry-run"], text);
  }
});

test("prompts, comments and characters changed by blogs", () => {
  const r = P.parse("# list\n$ aws s3 ls \u201cs3://my bucket/\u201d \u2013\u2013recursive");
  assert.equal(r.service, "s3");
  assert.deepEqual(r.args, ["s3://my bucket/", "--recursive"]);
  assert.equal(P.parse("PS C:\\work> aws sts get-caller-identity").command, "get-caller-identity");
  assert.equal(P.parse("C:\\work>aws sts get-caller-identity").service, "sts");
});

test("quotes and escapes", () => {
  assert.deepEqual(P.parse(`aws x y --a 'b c' --d "e \\"f\\"" --g h\\ i`).args, ["--a", "b c", "--d", 'e "f"', "--g", "h i"]);
});

test("pipes and redirects are removed with a warning", () => {
  const r = P.parse("aws ec2 describe-instances --output json | jq . > out.json 2>&1");
  assert.deepEqual(r.args, ["--output", "json"]);
  assert.deepEqual(r.warnings, [{ code: "removed", text: "| jq . > out.json 2>&1" }]);
});

test("extra lines are ignored with a warning", () => {
  const r = P.parse("aws sts get-caller-identity\naws s3 ls");
  assert.equal(r.command, "get-caller-identity");
  assert.deepEqual(r.warnings, [{ code: "extra_lines", n: 1 }]);
});

test("global options before the service, and wait commands", () => {
  const r = P.parse("aws --region ap-northeast-1 --debug ec2 wait instance-running --instance-ids i-1");
  assert.equal(r.service, "ec2");
  assert.equal(r.command, "wait instance-running");
  assert.deepEqual(r.args, ["--region", "ap-northeast-1", "--debug", "--instance-ids", "i-1"]);
});

test("errors", () => {
  assert.deepEqual(P.parse("ls -la"), { error: "not_aws" });
  assert.deepEqual(P.parse("aws ec2"), { error: "incomplete", service: "ec2" });
  assert.deepEqual(P.parse("aws ec2 --help"), { error: "incomplete", service: "ec2" });
});

// ---------- mapArgs ----------
const DETAIL = {
  positionals: [{ name: "source" }, { name: "dest" }],
  params: [
    { name: "--instance-ids", type: "list" },
    { name: "--dry-run", neg: "--no-dry-run", type: "boolean" },
    { name: "--recursive", type: "flag" },
    { name: "--acl", type: "enum", choices: ["private", "public-read"] },
    { name: "--filters", type: "json" },
    { name: "--name", type: "string" },
  ],
  pagination: [{ name: "--max-items", type: "number" }],
  global_options: [{ name: "--region", type: "string" }, { name: "--no-cli-pager", type: "flag" }],
};

test("values are mapped to the builder fields", () => {
  const m = P.mapArgs(DETAIL, ["src", "s3://b/", "--instance-ids", "i-1", "my id", "--no-dry-run", "--recursive",
    "--acl=private", "--name", "web", "--max-items", "10", "--region", "us-east-1", "--no-cli-pager"]);
  assert.deepEqual(m.values, {
    "pos:source": "src", "pos:dest": "s3://b/", "p:--instance-ids": 'i-1 "my id"', "p:--dry-run": "--no-dry-run",
    "p:--recursive": "1", "p:--acl": "private", "p:--name": "web", "p:--max-items": "10",
  });
  assert.deepEqual(m.globals, { "--region": "us-east-1", "--no-cli-pager": "1" });
  assert.deepEqual(m.warnings, []);
});

test("problems are reported as warnings", () => {
  const m = P.mapArgs(DETAIL, ["a", "b", "c", "--unknown", "x", "--acl", "public", "--name", "--filters", "Name=a", "Name=b",
    "--region", "$REGION"]);
  assert.deepEqual(m.warnings, [
    { code: "extra", value: "c" },
    { code: "unknown", name: "--unknown" },
    { code: "choice", name: "--acl", value: "public" },
    { code: "novalue", name: "--name" },
    { code: "joined", name: "--filters" },
    { code: "variable", name: "--region" },
  ]);
  assert.equal(m.values["p:--filters"], "Name=a Name=b");
});

// ---------- 検索欄での判定 ----------
test("looksLikeCommand / commandLine", () => {
  assert.equal(P.looksLikeCommand("aws ec2 describe-instances"), true);
  assert.equal(P.looksLikeCommand("ec2 stop"), false);
  assert.equal(P.looksLikeCommand("aws ec2"), false);
  assert.equal(P.commandLine("# c\n$ aws s3 ls \\\n --recursive"), "aws s3 ls --recursive");
  assert.equal(P.commandLine("nothing"), "");
});
