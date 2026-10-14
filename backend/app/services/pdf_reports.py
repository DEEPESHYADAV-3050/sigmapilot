from io import BytesIO
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable


def markdown_to_pdf(title: str, markdown: str) -> bytes:
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18*mm, leftMargin=18*mm, topMargin=17*mm, bottomMargin=17*mm, title=title, author="SigmaPilot")
    base = getSampleStyleSheet()
    base.add(ParagraphStyle(name="ReportTitle", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=25, textColor=colors.HexColor("#163d36"), alignment=TA_LEFT, spaceAfter=12))
    base.add(ParagraphStyle(name="ReportH2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=17, textColor=colors.HexColor("#176b58"), spaceBefore=14, spaceAfter=7))
    base.add(ParagraphStyle(name="ReportH3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10, leading=13, textColor=colors.HexColor("#273746"), spaceBefore=9, spaceAfter=5))
    base.add(ParagraphStyle(name="ReportBody", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=12, textColor=colors.HexColor("#25313b"), spaceAfter=4))
    base.add(ParagraphStyle(name="ReportBullet", parent=base["BodyText"], fontName="Helvetica", fontSize=8.5, leading=12, leftIndent=12, firstLineIndent=-7, textColor=colors.HexColor("#25313b"), spaceAfter=3))
    story = []
    for index, raw in enumerate(markdown.splitlines()):
        line = raw.strip()
        if not line:
            story.append(Spacer(1, 3))
        elif line.startswith("# "):
            story.append(Paragraph(escape(line[2:]), base["ReportTitle"]))
            story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#4cd6b0"), spaceAfter=10))
        elif line.startswith("## "):
            story.append(Paragraph(escape(line[3:]), base["ReportH2"]))
        elif line.startswith("### "):
            story.append(Paragraph(escape(line[4:]), base["ReportH3"]))
        elif line.startswith("- "):
            content = escape(line[2:]).replace("`", "")
            story.append(Paragraph("• " + content, base["ReportBullet"]))
        else:
            content = escape(line).replace("**", "").replace("`", "")
            story.append(Paragraph(content, base["ReportBody"]))
    doc.build(story)
    return buffer.getvalue()
