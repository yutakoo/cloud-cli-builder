"use strict";
// 値の引用符の付け方（app/static/quote.js）。
// Windows の形式は、cmd の解釈とプログラムの引数分割を真似て、元の値がそのまま1つの引数として渡るかを確かめる。
const test = require("node:test");
const assert = require("node:assert/strict");
const { quote, quoteArgv, splitList } = require("../../app/static/quote.js");

const TRICKY = ['{"Key":"a&b"}', "a%20b", "%PATH%", "C:\\dir\\", 'he said "hi" & bye', "a b", "x^y", "100%",
  'a\\"b', "(test)", "a|b>c", "plain-value", "", "s3://bucket/key", 'say "50%" now', "%%", "tab\there",
  "it's", "$HOME", "`date`", "$(whoami)", "a;b", "line1\nline2", "!x!"];

// ---------- bash / zsh ----------
// bash の単語の解釈を真似る（' の中はそのまま、\ は次の1文字、引用の外の空白で区切る）
function bashWords(s) {
  const out = []; let cur = "", has = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === "'") { const j = s.indexOf("'", i + 1); assert.ok(j > i, "unterminated quote"); cur += s.slice(i + 1, j); i = j; has = true; }
    else if (c === "\\") { cur += s[++i]; has = true; }
    else if (c === " ") { if (has) out.push(cur); cur = ""; has = false; }
    else { assert.ok(!/[;&|<>$`()"*?!]/.test(c), `unquoted special char ${c} in ${s}`); cur += c; has = true; }
  }
  if (has) out.push(cur);
  return out;
}

test("bash: safe values stay as they are", () => {
  for (const v of ["i-0123456789", "s3://bucket/key", "a=b,c=d", "ap-northeast-1", "100%"]) assert.equal(quote(v, "bash"), v);
});

test("bash: every value round-trips as one word", () => {
  for (const v of TRICKY) {
    for (const shell of ["bash", "oneline"]) {
      assert.deepEqual(bashWords(`aws x ${quote(v, shell)} --y`), ["aws", "x", v, "--y"], `${shell}: ${JSON.stringify(v)}`);
    }
  }
});

// ---------- PowerShell ----------
test("pwsh: single quotes, '' for a quote, no expansion", () => {
  assert.equal(quote("plain-value", "pwsh"), "plain-value");
  assert.equal(quote("a b", "pwsh"), "'a b'");
  assert.equal(quote("it's", "pwsh"), "'it''s'");
  assert.equal(quote("$env:HOME", "pwsh"), "'$env:HOME'");
  assert.equal(quote("", "pwsh"), "''");
});

// ---------- コマンドプロンプト / .bat ----------
const ENV = { PATH: "C:\\Windows" };
// cmd の % 展開（第1段階）
function percent(s, bat) {
  let out = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] !== "%") { out += s[i]; continue; }
    if (bat && s[i + 1] === "%") { out += "%"; i++; continue; }
    if (bat && /[0-9*]/.test(s[i + 1] || "")) { i++; continue; }      // %1 などはバッチの引数（ここでは空）
    const j = s.indexOf("%", i + 1);
    if (j > i) {
      const name = s.slice(i + 1, j);
      if (name in ENV) { out += ENV[name]; i = j; continue; }
      if (bat) { i = j; continue; }                                    // .bat では未定義の変数は消える
    }
    if (bat) continue;                                                 // .bat では対のない % は消える
    out += "%";
  }
  return out;
}
// cmd の ^ と引用（第2段階）。引用の外に & | < > が残っていれば失敗
function caret(s) {
  let out = "", q = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (q) { if (c === '"') q = false; out += c; continue; }
    if (c === "^") { out += s[++i] ?? ""; continue; }
    if (c === '"') { q = true; out += c; continue; }
    assert.ok(!"&|<>".includes(c), `unquoted ${c} in ${s}`);
    out += c;
  }
  return out;
}
// Windows のプログラムの引数分割（MSVCRT の規則）
function argv(s) {
  const args = []; let cur = "", inq = false, has = false;
  for (let i = 0; i < s.length;) {
    const c = s[i];
    if (c === "\\") {
      let n = 0; while (s[i] === "\\") { n++; i++; }
      if (s[i] === '"') { cur += "\\".repeat(n >> 1); if (n & 1) { cur += '"'; i++; } } else cur += "\\".repeat(n);
      has = true; continue;
    }
    if (c === '"') { if (inq && s[i + 1] === '"') { cur += '"'; i += 2; continue; } inq = !inq; has = true; i++; continue; }
    if (/[ \t]/.test(c) && !inq) { if (has) { args.push(cur); cur = ""; has = false; } i++; continue; }
    cur += c; has = true; i++;
  }
  if (has) args.push(cur);
  return args;
}

test("cmd / bat: every value reaches aws.exe unchanged as one argument", () => {
  for (const v of TRICKY.filter((x) => !x.includes("\n"))) {   // cmd は改行を値に入れられない
    for (const shell of ["cmd", "bat"]) {
      const line = `aws x ${quote(v, shell)} --y`;
      assert.deepEqual(argv(caret(percent(line, shell === "bat"))), ["aws", "x", v, "--y"], `${shell}: ${JSON.stringify(v)} -> ${line}`);
    }
  }
});

test("cmd / bat: % is written differently", () => {
  assert.equal(quote("a%20b", "cmd"), '^"a^%20b^"');
  assert.equal(quote("a%20b", "bat"), '^"a%%20b^"');
  assert.equal(quote("plain-value", "cmd"), "plain-value");
  assert.equal(quote("a b", "cmd"), '"a b"');
});

test("quoteArgv doubles backslashes only before a quote", () => {
  assert.equal(quoteArgv("C:\\dir\\file"), '"C:\\dir\\file"');
  assert.equal(quoteArgv("C:\\dir\\"), '"C:\\dir\\\\"');
  assert.equal(quoteArgv('a\\"b'), '"a\\\\\\"b"');
});

// ---------- リストの入力 ----------
test("splitList", () => {
  assert.deepEqual(splitList("i-1 i-2  i-3"), ["i-1", "i-2", "i-3"]);
  assert.deepEqual(splitList(`a "b c" 'd e'`), ["a", "b c", "d e"]);
  assert.deepEqual(splitList("   "), []);
});
