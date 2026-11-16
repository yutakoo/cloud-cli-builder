"""説明文の辞書翻訳（app/ja_docs.py）。"""
import json

from app import aws_catalog, ja_docs

BUILTIN = {k: v for k, v in json.loads(ja_docs.BUILTIN.read_text(encoding="utf-8")).items() if not k.startswith("//")}
KNOWN = next(iter(BUILTIN))                    # 手書きの辞書にある文


def test_sentences():
    assert ja_docs.sentences("First one. Second one.") == ["First one.", "Second one."]
    assert ja_docs.sentences("Use a tag, e.g. Name. Then go.") == ["Use a tag, e.g. Name.", "Then go."]
    assert ja_docs.sentences("Values (i.e. A or B) work. Done.") == ["Values (i.e. A or B) work.", "Done."]
    assert ja_docs.sentences("  spaced\n text  ") == ["spaced text"]
    assert ja_docs.sentences("") == []


def test_missing_returns_unknown_sentences_once_in_order():
    texts = [KNOWN, "Alpha unknown. " + KNOWN, "Beta unknown.", "Alpha unknown."]
    assert ja_docs.missing(texts) == ["Alpha unknown.", "Beta unknown."]


def test_waiter_descriptions_need_no_claude():
    """wait コマンドの説明は、決まった形の文のパターンだけで訳せる。"""
    docs = [aws_catalog.command_detail("ec2", c)["description"]
            for c in ("wait instance-running", "wait instance-exists")]
    assert ja_docs.missing(docs) == []
    ja = ja_docs.translate(docs[0], {})
    assert "15 秒ごと" in ja and "終了コード 255" in ja


def test_translate_uses_dictionary_extra_and_keeps_unknown():
    text = f"{KNOWN} Gamma unknown. Delta unknown."
    out = ja_docs.translate(text, {"Gamma unknown.": "ガンマ"})
    assert out == BUILTIN[KNOWN] + "ガンマ" + "Delta unknown."


def test_translate_keeps_spaces_between_english_sentences():
    assert ja_docs.translate("Alpha unknown. Beta unknown.", {}) == "Alpha unknown. Beta unknown."
    assert ja_docs.translate("Alpha unknown. Beta unknown.", {"Alpha unknown.": "アルファ。"}) == "アルファ。Beta unknown."
    assert ja_docs.translate("Alpha unknown. Beta unknown.", {"Beta unknown.": "ベータ。"}) == "Alpha unknown.ベータ。"


def test_learn_saves_and_is_used(learned):
    ja_docs.learn({"Epsilon unknown.": "イプシロン", "": "x", "Empty value.": ""})
    saved = json.loads(learned.read_text(encoding="utf-8"))
    assert saved == {"Epsilon unknown.": "イプシロン"}
    assert ja_docs.missing(["Epsilon unknown."]) == []
    assert ja_docs.translate("Epsilon unknown.", {}) == "イプシロン"


def test_builtin_wins_over_learned():
    ja_docs.learn({KNOWN: "上書きしようとした訳"})
    assert ja_docs.table()[KNOWN] == BUILTIN[KNOWN]


def test_learn_can_be_disabled(learned, monkeypatch):
    monkeypatch.setattr(ja_docs, "LEARN", False)
    ja_docs.learn({"Zeta unknown.": "ゼータ"})
    assert not learned.exists()
