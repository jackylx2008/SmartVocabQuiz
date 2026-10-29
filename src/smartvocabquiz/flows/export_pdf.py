"""把人工勾选的已审核题目排成适合纸笔作答的 A4 PDF。"""

from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import BaseDocTemplate, Frame, KeepTogether, PageTemplate, Paragraph, Spacer


SECTION_ORDER = ("热身认读", "拼写巩固", "语境读写")


def _font() -> str:
    if "SmartVocabChinese" in pdfmetrics.getRegisteredFontNames():
        return "SmartVocabChinese"
    local_font = Path("C:/Windows/Fonts/simhei.ttf")
    if local_font.is_file():
        pdfmetrics.registerFont(TTFont("SmartVocabChinese", str(local_font)))
    else:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        return "STSong-Light"
    return "SmartVocabChinese"


def _paragraph(value: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(escape(value).replace("\n", "<br/>"), style)


def _question_lines(question: dict[str, Any]) -> list[tuple[str, str]]:
    kind = question["type"]
    if kind == "T1_ODD_ONE_OUT":
        options = [f"{chr(65 + index)}. {word}" for index, word in enumerate(question["options"])]
        choices = "   |   ".join(options)
        return [("圈出不同类的单词。", "prompt"), (choices, "english")]
    if kind == "T2_READ_AND_MATCH":
        options = [f"{chr(65 + index)}. {word}" for index, word in enumerate(question["options"])]
        choices = "   |   ".join(options)
        return [("根据特征选词。", "prompt"), (question["clue"], "english"),
                (choices, "english"), ("答案：__________", "writing")]
    if kind == "T10_CN_EN_MATCH":
        options = [f"{chr(65 + index)}. {word}" for index, word in enumerate(question["options"])]
        choices = "   |   ".join(options)
        return [(f"选择对应的英文单词：{question['cn_meaning']}", "prompt"),
                (choices, "english"), ("答案：__________", "writing")]
    if kind == "T4_MISSING_LETTERS":
        return [(f"根据中文补全字母：{question['cn_meaning']}", "prompt"),
                (question["masked_word"], "english-large"),
                ("缺失字母：__________", "writing")]
    if kind == "T5_UNSCRAMBLE_WORDS":
        return [(f"看中文，重新组合字母：{question['cn_meaning']}", "prompt"),
                (question["scrambled"], "english-large"),
                ("完整单词：____________________", "writing")]
    if kind == "T6_WORD_BANK_FILL":
        return [("从词库选词并抄写。", "prompt"),
                ("   |   ".join(question["word_bank"]), "english"),
                (question["cn_translation"], "prompt"), (question["sentence"], "english-large")]
    if kind == "T11_DIALOGUE_REPLY":
        return [(question["scene_cn"], "prompt"), (question["question"], "english"),
                *[(f"{chr(65 + index)}. {option}", "english")
                  for index, option in enumerate(question["options"])],
                ("选择：__________", "writing")]
    raise ValueError(f"暂不支持导出题型：{kind}")


def export_reviewed_pdf(candidates: list[dict[str, Any]], target: Path) -> None:
    if not candidates:
        raise ValueError("请先勾选至少一道已审核题目")
    font = _font()
    styles = {
        "section": ParagraphStyle("quiz-section", fontName=font, fontSize=15, leading=21,
                                  spaceBefore=10, spaceAfter=8,
                                  textColor=colors.HexColor("#276350")),
        "prompt": ParagraphStyle("quiz-prompt", fontName=font, fontSize=13, leading=19,
                                 spaceAfter=4, wordWrap="CJK", textColor=colors.HexColor("#1C4639")),
        "english": ParagraphStyle("quiz-english", fontName="Helvetica", fontSize=16,
                                  leading=23, leftIndent=18, spaceAfter=4,
                                  textColor=colors.HexColor("#172820")),
        "english-large": ParagraphStyle("quiz-english-large", fontName="Helvetica-Bold", fontSize=18,
                                        leading=26, leftIndent=18, spaceAfter=4,
                                        textColor=colors.HexColor("#172820")),
        "writing": ParagraphStyle("quiz-writing", fontName=font, fontSize=13, leading=20,
                                  leftIndent=18, spaceAfter=4, wordWrap="CJK"),
    }
    story: list[Any] = []
    ordered = sorted(candidates, key=lambda item: (
        SECTION_ORDER.index(item["section_title"])
        if item["section_title"] in SECTION_ORDER else len(SECTION_ORDER),
        item["approved_at"], item["id"]))
    last_section = None
    for number, candidate in enumerate(ordered, 1):
        section = candidate["section_title"]
        heading = None
        if section != last_section:
            heading = _paragraph(section, styles["section"])
            last_section = section
        lines = [_paragraph(f"{number}.  {text}" if index == 0 else text, styles[kind])
                 for index, (text, kind) in enumerate(_question_lines(candidate["question"]))]
        if heading is not None:
            lines.insert(0, heading)
        lines.append(Spacer(1, 11))
        story.append(KeepTogether(lines))

    page_width, page_height = landscape(A4)

    def page_decoration(canvas: Any, document: Any) -> None:
        canvas.saveState()
        canvas.setFont(font, 20)
        canvas.setFillColor(colors.HexColor("#173E35"))
        canvas.drawString(36, page_height - 34, "英语词汇练习")
        canvas.setFont(font, 11)
        canvas.setFillColor(colors.HexColor("#53685E"))
        canvas.drawRightString(page_width - 36, page_height - 33, f"共 {len(candidates)} 题")
        canvas.drawString(36, page_height - 57, "姓名：________________    日期：________________")
        canvas.setStrokeColor(colors.HexColor("#D8E8DC"))
        canvas.line(36, page_height - 68, page_width - 36, page_height - 68)
        canvas.setFont(font, 9)
        canvas.setFillColor(colors.HexColor("#82978A"))
        canvas.drawString(36, 22, "SmartVocabQuiz  -  纸笔练习")
        canvas.drawRightString(page_width - 36, 22, str(document.page))
        canvas.restoreState()

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    try:
        document = BaseDocTemplate(str(temporary), pagesize=(page_width, page_height),
                                   title="英语词汇练习", author="SmartVocabQuiz")
        gutter = 24
        column_width = (page_width - 72 - gutter) / 2
        frames = [Frame(36 + index * (column_width + gutter), 38, column_width,
                        page_height - 114, leftPadding=0, rightPadding=0,
                        topPadding=0, bottomPadding=0)
                  for index in range(2)]
        template = PageTemplate(id="landscape", frames=frames, onPage=page_decoration)
        document.addPageTemplates(template)
        document.build(story)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
