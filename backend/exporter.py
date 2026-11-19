"""Render an immutable trip-plan snapshot as PDF or date-based PNG files."""
from __future__ import annotations

import logging
import os
import re
import zipfile
from datetime import datetime
from html import escape
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core import ApiError, iso, now
from db import Job, PlanExport, SessionLocal

log = logging.getLogger(__name__)


def font_path() -> Path:
    # 저장소에 넣은 NanumGothic(SIL OFL, fonts/OFL.txt)을 기본으로 쓴다.
    candidates = [
        os.getenv("EXPORT_FONT_PATH"),
        Path(__file__).parent / "fonts" / "NanumGothic-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise ApiError(500, "EXPORT_FONT_UNAVAILABLE", "한글 내보내기 글꼴이 설치되지 않았습니다.")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", value).strip("-")
    return (cleaned[:48] or "trip-plan")


def time_text(value: str | None) -> str:
    return value[11:16] if value else "미확인"


def money(value) -> str:
    return f"{int(value):,}원" if isinstance(value, (int, float)) else "미확인"


MODES = {"CAR": "자동차", "PUBLIC_TRANSIT": "대중교통", "BUS": "버스", "SUBWAY": "지하철",
         "TRAIN": "열차", "EXPRESS_BUS": "고속버스", "INTERCITY_BUS": "시외버스",
         "WALK": "도보", "TRANSFER_BUFFER": "승차 준비시간(가정)"}


def route_lines(snapshot: dict, leg: dict) -> list[str]:
    stops = {s.get("id"): s for d in snapshot.get("days", []) for s in d.get("stops", [])}
    origin = snapshot.get("inputSnapshot", {}).get("origin") or {}
    def label(identifier):
        if identifier == "ORIGIN":
            return origin.get("label") or "출발지"
        return stops.get(identifier, {}).get("name") or "장소 미확인"
    lines = [f"{label(leg.get('fromStopId'))} → {label(leg.get('toStopId'))}", leg_text(leg),
             f"예상 출발 {time_text(leg.get('departureAt'))} / 도착 {time_text(leg.get('arrivalAt'))}"]
    for segment in leg.get("segments") or []:
        route = f"{segment.get('fromName') or '출발 지점 미확인'} → {segment.get('toName') or '도착 지점 미확인'}"
        names = [str(lane.get("busNo") or lane.get("name")) for lane in segment.get("lines", [])
                 if lane.get("busNo") or lane.get("name")]
        lines.append(f"  {route} · {leg_text(segment)}" + (f" · {', '.join(names)}" if names else ""))
        if segment.get("walkabilityStatus") == "UNCONFIRMED":
            lines.append("  직선거리 기반 도보 추정 · 실제 보행 가능 여부 미확인")
    return lines


def leg_text(leg: dict) -> str:
    duration = leg.get("durationMinutes")
    distance = leg.get("distanceMeters")
    parts = [MODES.get(leg.get("mode"), "이동")]
    parts.append(f"{duration}분" if duration is not None else "시간 미확인")
    parts.append(f"{int(distance):,}m" if distance is not None else "거리 미확인")
    if leg.get("serviceTimeStatus") == "UNCONFIRMED":
        parts.append("실제 운행 시각 미확인")
    return " · ".join(parts)


def selected_days(snapshot: dict, dates: list[str]) -> list[dict]:
    wanted = set(dates)
    return [day for day in snapshot["days"] if day["date"] in wanted]


def register_pdf_font() -> str:
    name = "FestivalExportKorean"
    if name not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(name, str(font_path())))
    return name


def render_pdf(snapshot: dict, dates: list[str], output: BytesIO, generated_at: datetime) -> None:
    font = register_pdf_font()
    styles = getSampleStyleSheet()
    title = ParagraphStyle("TripTitle", parent=styles["Title"], fontName=font, fontSize=22,
                           leading=30, textColor=colors.HexColor("#3F2B68"), alignment=TA_CENTER)
    heading = ParagraphStyle("Heading", parent=styles["Heading2"], fontName=font, fontSize=15,
                             leading=21, textColor=colors.HexColor("#3F2B68"), spaceBefore=5*mm)
    body = ParagraphStyle("Body", parent=styles["BodyText"], fontName=font, fontSize=9.5,
                          leading=15, textColor=colors.HexColor("#263238"))
    small = ParagraphStyle("Small", parent=body, fontSize=8, leading=12, textColor=colors.HexColor("#607D8B"))
    doc = SimpleDocTemplate(output, pagesize=A4, leftMargin=17*mm, rightMargin=17*mm,
                            topMargin=17*mm, bottomMargin=17*mm, title=snapshot["title"])
    story = [Paragraph(escape(snapshot["title"]), title), Spacer(1, 3*mm),
             Paragraph(f"계획 버전 {snapshot['version']} · 상태 {escape(snapshot['status'])}", body),
             Paragraph(f"파일 생성 {escape(iso(generated_at))}", small), Spacer(1, 5*mm)]
    days = selected_days(snapshot, dates)
    for day_index, day in enumerate(days):
        if day_index:
            story.append(PageBreak())
        trip_day_number = snapshot["days"].index(day) + 1
        story.append(Paragraph(f"DAY {trip_day_number} · {escape(day['date'])}", heading))
        if not day.get("stops"):
            story.append(Paragraph("등록된 일정이 없습니다.", body))
        for index, stop in enumerate(day.get("stops", []), 1):
            schedule = f"{time_text(stop.get('startAt'))} - {time_text(stop.get('endAt'))}"
            details = [f"{index}. <b>{escape(str(stop.get('name') or '이름 미확인'))}</b>",
                       f"{escape(schedule)} · 비용 {escape(money(stop.get('costKrw')))}"]
            if stop.get("recommendationReason"):
                details.append(f"추천 이유: {escape(stop['recommendationReason'])}")
            if stop.get("address"):
                details.append(f"주소: {escape(stop['address'])}")
            if stop.get("sourceUrl"):
                details.append(f"출처: {escape(stop['sourceUrl'])}")
            block = Table([[Paragraph("<br/>".join(details), body)]], colWidths=[171*mm])
            block.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F0FF")),
                                       ("BOX", (0, 0), (-1, -1), .5, colors.HexColor("#D8C9F0")),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 8),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                                       ("TOPPADDING", (0, 0), (-1, -1), 7),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
            story.extend([KeepTogether(block), Spacer(1, 2*mm)])
        if day.get("legs"):
            story.append(Paragraph("이동 구간", heading))
            rows = [[Paragraph("순서", body), Paragraph("경로 정보", body)]]
            for i, leg in enumerate(day["legs"], 1):
                for j, line in enumerate(route_lines(snapshot, leg)):
                    rows.append([Paragraph(str(i) if j == 0 else "", body), Paragraph(escape(line), body)])
            table = Table(rows, colWidths=[16*mm, 155*mm], repeatRows=1)
            table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8DDF8")),
                                       ("GRID", (0, 0), (-1, -1), .4, colors.HexColor("#C7B5E3")),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP"),
                                       ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                       ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                       ("TOPPADDING", (0, 0), (-1, -1), 5),
                                       ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
            story.append(table)
    story.append(Paragraph("예산", heading))
    budget = snapshot.get("budget", {})
    story.append(Paragraph(
        f"확인된 비용 {money(budget.get('knownTotalKrw'))} · 미확인 {budget.get('unknownItemCount', 0)}건"
        + (f" · 입력 예산 {money(budget.get('limitKrw'))}" if budget.get("limitKrw") is not None else ""), body))
    if snapshot.get("warnings"):
        story.append(Paragraph("확인할 사항", heading))
        for warning in snapshot["warnings"]:
            story.append(Paragraph(f"• {escape(warning.get('message', ''))}", body))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont(font, 8)
        canvas.setFillColor(colors.HexColor("#78909C"))
        canvas.drawCentredString(A4[0] / 2, 9*mm, f"축제잇길 · 계획 v{snapshot['version']} · {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


def pil_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(font_path()), size=size)


def wrap_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, current = [], ""
    for char in text:
        candidate = current + char
        if current and draw.textlength(candidate, font=font) > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def render_day_png(snapshot: dict, day: dict, day_number: int, output: BytesIO, generated_at: datetime) -> None:
    width, margin, content_width = 1400, 90, 1220
    title_font, heading_font, body_font, small_font = pil_font(48), pil_font(31), pil_font(24), pil_font(19)
    probe = Image.new("RGB", (width, 100), "white")
    draw = ImageDraw.Draw(probe)
    blocks = []
    for stop in day.get("stops", []):
        reason = stop.get("recommendationReason") or ""
        reason_lines = wrap_lines(draw, reason, small_font, content_width - 80) if reason else []
        source = " · ".join(filter(None, [f"주소: {stop['address']}" if stop.get("address") else "",
                                           f"출처: {stop['sourceUrl']}" if stop.get("sourceUrl") else ""]))
        source_lines = wrap_lines(draw, source, small_font, content_width - 80) if source else []
        blocks.append((stop, 125 + (len(reason_lines) + len(source_lines)) * 28, reason_lines, source_lines))
    warning_lines = [line for w in snapshot.get("warnings", [])
                     for line in wrap_lines(draw, "• " + w.get("message", ""), small_font, content_width - 40)]
    leg_lines = [wrap_lines(draw, f"{i}. {line}" if j == 0 else line, small_font, content_width - 40)
                 for i, leg in enumerate(day.get("legs", []), 1)
                 for j, line in enumerate(route_lines(snapshot, leg))]
    height = 540 + sum(h + 22 for _, h, _, _ in blocks) + sum(len(x) * 30 for x in leg_lines)
    height += (100 + len(warning_lines) * 28) if warning_lines else 0
    image = Image.new("RGB", (width, max(height, 900)), "#F7F4FB")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 45, width - 45, image.height - 45), radius=32, fill="white", outline="#D8C9F0", width=3)
    y = 95
    draw.text((margin, y), snapshot["title"], font=title_font, fill="#3F2B68")
    y += 78
    draw.text((margin, y), f"DAY {day_number} · {day['date']}", font=heading_font, fill="#5D3B8C")
    y += 55
    draw.text((margin, y), f"계획 v{snapshot['version']} · {snapshot['status']} · 생성 {iso(generated_at)}",
              font=small_font, fill="#607D8B")
    y += 62
    for index, (stop, block_height, reason_lines, source_lines) in enumerate(blocks, 1):
        draw.rounded_rectangle((margin, y, width - margin, y + block_height), radius=22, fill="#F5F0FF")
        draw.text((margin + 30, y + 22), f"{index}. {stop.get('name') or '이름 미확인'}", font=heading_font, fill="#263238")
        draw.text((margin + 30, y + 68),
                  f"{time_text(stop.get('startAt'))} - {time_text(stop.get('endAt'))} · 비용 {money(stop.get('costKrw'))}",
                  font=small_font, fill="#546E7A")
        line_y = y + 102
        for line in reason_lines:
            draw.text((margin + 30, line_y), line, font=small_font, fill="#5D3B8C")
            line_y += 28
        for line in source_lines:
            draw.text((margin + 30, line_y), line, font=small_font, fill="#607D8B")
            line_y += 28
        y += block_height + 22
    if leg_lines:
        draw.text((margin, y), "이동 구간", font=heading_font, fill="#3F2B68")
        y += 48
        for lines in leg_lines:
            for line in lines:
                draw.text((margin + 15, y), line, font=small_font, fill="#37474F")
                y += 30
    y += 18
    budget = snapshot.get("budget", {})
    draw.text((margin, y), "예산", font=heading_font, fill="#3F2B68")
    y += 48
    budget_text = (f"확인된 비용 {money(budget.get('knownTotalKrw'))} · 미확인 {budget.get('unknownItemCount', 0)}건"
                   + (f" · 입력 예산 {money(budget.get('limitKrw'))}" if budget.get("limitKrw") is not None else ""))
    draw.text((margin + 15, y), budget_text, font=small_font, fill="#37474F")
    y += 36
    if warning_lines:
        y += 18
        draw.text((margin, y), "확인할 사항", font=heading_font, fill="#8A4F16")
        y += 48
        for line in warning_lines:
            draw.text((margin + 15, y), line, font=small_font, fill="#6D4C41")
            y += 28
    image.crop((0, 0, width, min(image.height, y + 90))).save(output, format="PNG", optimize=True)


def create_export_file(record: PlanExport) -> tuple[bytes, str, str]:
    base = f"{safe_name(record.snapshot['title'])}-v{record.plan_version}"
    generated_at = now()
    output = BytesIO()
    if record.format == "PDF":
        render_pdf(record.snapshot, record.selected_dates, output, generated_at)
        return output.getvalue(), "application/pdf", f"{base}.pdf"
    days = selected_days(record.snapshot, record.selected_dates)
    if len(days) == 1:
        day_number = record.snapshot["days"].index(days[0]) + 1
        render_day_png(record.snapshot, days[0], day_number, output, generated_at)
        return output.getvalue(), "image/png", f"{base}-{days[0]['date']}.png"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for day in days:
            index = record.snapshot["days"].index(day) + 1
            png = BytesIO()
            render_day_png(record.snapshot, day, index, png, generated_at)
            archive.writestr(f"DAY-{index}-{day['date']}.png", png.getvalue())
    return output.getvalue(), "application/zip", f"{base}-images.zip"


def run_export(export_id: str) -> None:
    with SessionLocal() as db:
        try:
            record = db.get(PlanExport, export_id)
            if record is None:
                return
            # 시간 초과로 이미 실패 처리된 작업은 다시 시작하지 않는다(QUEUED일 때만 시작).
            started = now()
            if db.query(PlanExport).filter(PlanExport.id == export_id, PlanExport.status == "QUEUED").update(
                    {"status": "RUNNING", "updated_at": started}) == 0:
                db.rollback()
                return
            db.query(Job).filter(Job.id == record.job_id).update(
                {"status": "RUNNING", "stage": "EXPORTING", "updated_at": started})
            db.commit()
            content, content_type, filename = create_export_file(record)
            # 시간 초과로 이미 실패 처리됐으면 덮어쓰지 않는다(RUNNING일 때만 완료로 바꾼다).
            done = now()
            saved = db.query(PlanExport).filter(PlanExport.id == export_id, PlanExport.status == "RUNNING").update(
                {"content": content, "content_type": content_type, "filename": filename,
                 "status": "SUCCEEDED", "updated_at": done})
            saved += db.query(Job).filter(Job.id == record.job_id, Job.status == "RUNNING").update(
                {"status": "SUCCEEDED", "result": {"exportId": export_id}, "updated_at": done})
            if saved == 2:
                db.commit()
            else:
                db.rollback()
            return
        except ApiError as exc:
            log.warning("export failed: %s %s", export_id, exc.code)
            error = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
        except Exception:
            log.exception("export failed: %s", export_id)
            error = {"code": "EXPORT_FAILED", "message": "파일을 만들지 못했습니다.", "retryable": True}
        db.rollback()
        record = db.get(PlanExport, export_id)
        if record is None:  # 내보내는 중에 계획·계정이 지워졌다
            return
        job = db.get(Job, record.job_id)
        record.status = job.status = "FAILED"
        record.error = job.error = error
        record.updated_at = job.updated_at = now()
        db.commit()
