"use strict";
// コマンドの値に、シェルの形式に合わせた引用符を付ける（実行はしない。文字列を組み立てるだけ）。
// quote(value, shell)  shell: "bash" / "pwsh" / "cmd" / "bat" / "oneline"（oneline は bash と同じ）
// splitList(text)      スペース区切りの入力を値の配列にする（"..." や '...' で囲めば空白を含められる）
(function (root) {
  const SAFE = /^[A-Za-z0-9_@%+=:,./~\-]+$/;
  const SAFE_WIN = /^[A-Za-z0-9_@+=:,./~\-]+$/;   // cmd では % が環境変数・バッチ引数として展開されるので外す
  function quote(v, shell) {
    v = String(v);
    if (shell === "cmd" || shell === "bat") return quoteWin(v, shell === "bat");
    if (v !== "" && SAFE.test(v)) return v;
    if (shell === "pwsh") return `'${v.replace(/'/g, "''")}'`;
    return `'${v.replace(/'/g, `'\\''`)}'`;
  }
  // Windows の引数の規則（aws.exe が引数を分ける規則）で "..." に入れる。" は \" に、直後に " が来る \ は2倍にする
  function quoteArgv(v) {
    let out = '"', bs = 0;
    for (const c of v) {
      if (c === "\\") { bs++; continue; }
      out += "\\".repeat(c === '"' ? bs * 2 + 1 : bs) + c;
      bs = 0;
    }
    return out + "\\".repeat(bs * 2) + '"';
  }
  // コマンドプロンプト（bat=false）／ .bat ファイル（bat=true）用。
  // cmd は \" を特別扱いせず " のたびに引用の内外が切り替わるので、" や % を含む値は
  // 記号をすべて ^ でエスケープし、cmd に引用の外として読ませる（& | > などが効かないようにする）。
  // % は .bat では %%、コマンドプロンプトでは ^%（未定義の変数名として展開されずに残る）にする
  function quoteWin(v, bat) {
    if (v !== "" && SAFE_WIN.test(v)) return v;
    const q = quoteArgv(v);
    if (!/["%]/.test(v)) return q;   // " と % がなければ、"..." の中の & | > などは cmd でもそのまま渡る
    return q.replace(/[\^&|<>()"%]/g, (c) => (c === "%" ? (bat ? "%%" : "^%") : "^" + c));
  }
  function splitList(v) {
    // スペース区切り（"..." や '...' で囲めば空白を含められる）
    return (v.match(/"[^"]*"|'[^']*'|\S+/g) || []).map((t) => t.replace(/^(["'])(.*)\1$/, "$2"));
  }

  const api = { quote, quoteArgv, quoteWin, splitList };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ShellQuote = api;
})(typeof window !== "undefined" ? window : globalThis);
