"use strict";
// 貼り付けた AWS CLI コマンドを読み取る（実行はしない。文字列として解析するだけ）。
// parse(text)            → { service, command, args, warnings } または { error }
// mapArgs(detail, args)  → { values, globals, warnings }（ビルダーの入力欄の形に変換）
(function (root) {
  // 値を取らない共通オプション（--region などは次の語を値として取る）
  const GLOBAL_FLAGS = new Set(["--debug", "--no-verify-ssl", "--no-paginate", "--no-cli-pager", "--no-sign-request",
    "--cli-auto-prompt", "--no-cli-auto-prompt"]);
  const PROMPT = /^(?:\$|%|>|PS(?: [^>]*)?>|[A-Za-z]:\\[^>]*>)\s*/;   // $ / % / > / PS C:\> / C:\work>
  const VARIABLE = /\$\{?\w|\$\(|`|%\w+%/;                          // $VAR ${VAR} $(...) `...` %VAR%

  // 行継続をつなぎ、コメント・プロンプトを除いて「コマンドの行」の一覧にする
  function normalize(text) {
    let t = String(text || "").replace(/\r\n?/g, "\n")
      // ブログ・Word などで書き換えられた文字を元に戻す
      .replace(/[\u200b-\u200d\ufeff]/g, "")                       // ゼロ幅の文字
      .replace(/[\u00a0\u2007\u202f\u3000]/g, " ")                 // ノーブレークスペース・全角スペース
      .replace(/[\u201c\u201d\u201e\u201f\u2033\uff02]/g, '"')       // “ ” ＂
      .replace(/[\u2018\u2019\u201a\u201b\u2032\uff07]/g, "'")       // ‘ ’ ＇
      .replace(/(^|[\s=])[\u2010-\u2015\u2212\uff0d]{1,2}(?=[A-Za-z])/g, "$1--");   // –region / —region → --region
    const mode = /`[ \t]*\n/.test(t) ? "pwsh" : /\^[ \t]*\n/.test(t) ? "cmd" : "sh";
    t = t.replace(/[ \t]*(?:\\|`|\^)[ \t]*\n[ \t]*/g, " ");
    const lines = t.split("\n").map((l) => l.trim().replace(PROMPT, "").trim())
      .filter((l) => l && !l.startsWith("#") && !/^rem\s/i.test(l));
    return { lines, mode };
  }

  // シェル風の字句解析。| ; & < > が引用符の外に出たら、そこから先は「外した部分」として返す
  function tokenize(s, mode) {
    const toks = [];
    let cur = null, i = 0, rest = "";
    const add = (x) => { cur = (cur ?? "") + x; };
    const push = () => { if (cur !== null) { toks.push(cur); cur = null; } };
    while (i < s.length) {
      const c = s[i];
      if (c === "'") {
        const j = s.indexOf("'", i + 1);
        if (j < 0) { add(s.slice(i + 1)); i = s.length; break; }
        add(s.slice(i + 1, j)); i = j + 1;
      } else if (c === '"') {
        let j = i + 1, buf = "";
        while (j < s.length && s[j] !== '"') {
          if (s[j] === "\\" && mode === "sh" && j + 1 < s.length && '"\\$`'.includes(s[j + 1])) { buf += s[j + 1]; j += 2; }
          else if (mode === "pwsh" && s[j] === "`" && j + 1 < s.length) { buf += s[j + 1]; j += 2; }
          else buf += s[j++];
        }
        add(buf); i = j + 1;
      } else if (c === "\\" && mode === "sh" && i + 1 < s.length) {
        add(s[i + 1]); i += 2;
      } else if (/\s/.test(c)) {
        push(); i++;
      } else if (/[|;&<>]/.test(c)) {
        push(); rest = s.slice(i).trim(); break;
      } else {
        add(c); i++;
      }
    }
    push();
    if (/^>/.test(rest) && /^\d$/.test(toks[toks.length - 1] || "")) rest = toks.pop() + rest;   // 2>&1 など
    return { tokens: toks, rest };
  }

  function parse(text) {
    const { lines, mode } = normalize(text);
    const idx = lines.findIndex((l) => /^aws(\s|$)/.test(l));
    if (idx < 0) return { error: "not_aws" };
    const warnings = [];
    if (lines.length > 1) warnings.push({ code: "extra_lines", n: lines.length - 1 });
    const { tokens, rest } = tokenize(lines[idx], mode);
    if (rest) warnings.push({ code: "removed", text: rest });
    tokens.shift();   // "aws"
    const pre = [];   // サービス名より前に書かれた共通オプション（aws --region x ec2 ...）
    while (tokens.length && tokens[0].startsWith("--")) {
      const o = tokens.shift();
      pre.push(o);
      if (!o.includes("=") && !GLOBAL_FLAGS.has(o) && tokens.length && !tokens[0].startsWith("--")) pre.push(tokens.shift());
    }
    const service = tokens.shift();
    let command = tokens.shift();
    if (!service || !command || command.startsWith("-")) return { error: "incomplete", service };
    if (command === "wait" && tokens.length && !tokens[0].startsWith("-")) command += " " + tokens.shift();
    return { service, command, args: [...pre, ...tokens], warnings };
  }

  // 読み取った引数を、ビルダーの入力値（S.values / S.globals と同じ形）に変換する
  function mapArgs(d, args) {
    const values = {}, globals = {}, warnings = [];
    const opts = new Map();
    for (const p of [...(d.params || []), ...(d.pagination || [])]) {
      opts.set(p.name, { p, scope: "p" });
      if (p.neg) opts.set(p.neg, { p, scope: "p" });
    }
    for (const g of d.global_options || []) opts.set(g.name, { p: g, scope: "g" });
    const pos = d.positionals || [];
    let posI = 0;
    for (let i = 0; i < args.length; i++) {
      let a = args[i], inline = null;
      // 値を読む。many=false なら1語だけ（文字列・数値・選択）、true なら次のオプションまで全部（リスト・JSON）
      const take = (many = true) => {
        if (inline !== null) return [inline];
        const vs = [];
        while (i + 1 < args.length && !args[i + 1].startsWith("--")) { vs.push(args[++i]); if (!many) break; }
        return vs;
      };
      if (!a.startsWith("--")) {
        if (posI < pos.length) {
          const p = pos[posI++];
          values["pos:" + p.name] = a;
          if (VARIABLE.test(a)) warnings.push({ code: "variable", name: p.name });
        } else warnings.push({ code: "extra", value: a });
        continue;
      }
      const eq = a.indexOf("=");
      if (eq > 0) { inline = a.slice(eq + 1); a = a.slice(0, eq); }
      const hit = opts.get(a);
      if (!hit) { take(); warnings.push({ code: "unknown", name: a }); continue; }
      const { p, scope } = hit;
      let v;
      if (p.type === "boolean") v = a;   // --dry-run / --no-dry-run をそのまま
      else if (p.type === "flag") v = "1";
      else {
        const vs = take(p.type === "list" || p.type === "json");
        if (!vs.length) { warnings.push({ code: "novalue", name: a }); continue; }
        if (p.type === "list") v = vs.map((x) => (/\s/.test(x) ? `"${x}"` : x)).join(" ");
        else {
          if (vs.length > 1) warnings.push({ code: "joined", name: a });
          v = vs.join(" ");
        }
        if (p.type === "enum" && p.choices && !p.choices.includes(v)) warnings.push({ code: "choice", name: a, value: v });
      }
      if (VARIABLE.test(v)) warnings.push({ code: "variable", name: a });
      if (scope === "g") globals[p.name] = v; else values["p:" + p.name] = v;
    }
    return { values, globals, warnings };
  }

  // 検索欄に入っている文字が AWS CLI のコマンドらしいか
  function looksLikeCommand(text) {
    return normalize(text).lines.some((l) => /^aws\s+\S+\s+\S+/.test(l));
  }
  // 検索用に、コマンドの行（aws から始まる1行）を取り出す
  function commandLine(text) {
    return normalize(text).lines.find((l) => /^aws(\s|$)/.test(l)) || "";
  }

  const api = { parse, mapArgs, normalize, tokenize, looksLikeCommand, commandLine };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.PasteCmd = api;
})(typeof window !== "undefined" ? window : globalThis);
