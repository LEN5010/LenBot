"""Render saved single-group report data with Pillow; no model or network I/O."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from .report import SingleGroupReport
from len_bot.cards.tokens import THEME
from len_bot.cards.layout import split_pages


WIDTH=1080
MARGIN=54
ACCENT=THEME.accent
INK=THEME.ink
MUTED=THEME.muted
BACKGROUND=THEME.canvas


def render_report(report: SingleGroupReport,font_path: Path, *, page_index: int = 1, page_count: int = 1) -> bytes:
    fonts={size:ImageFont.truetype(str(font_path),size=size) for size in (18,20,22,25,28,32,38,48)}
    measuring=ImageDraw.Draw(Image.new('RGB',(1,1)))
    elements=[]

    def rect(box,fill,radius=18):elements.append(('rect',box,fill,radius))
    def line(points,fill,width=2):elements.append(('line',points,fill,width))
    def text(value,x,y,width,size=28,color=INK):
        font=fonts[size]
        height=size+14
        paragraphs=str(value).split('\n')
        for paragraph in paragraphs:
            current=''
            for char in paragraph:
                candidate=current+char
                if current and measuring.textlength(candidate,font=font)>width:
                    elements.append(('text',(x,y),current,font,color))
                    y+=height;current=char
                else:current=candidate
            elements.append(('text',(x,y),current,font,color))
            y+=height
        return y

    zone=ZoneInfo(report.range.timezone)
    start=report.range.start_at.astimezone(zone)
    end=report.range.end_at.astimezone(zone)
    snapshot=datetime.fromtimestamp(report.range.snapshot_at,zone)
    rect((0,0,WIDTH,16),ACCENT,0)
    y=text('本群聊天报告',MARGIN,50,WIDTH-2*MARGIN,48)
    y=text(f'{start:%Y-%m-%d %H:%M}  →  {end:%Y-%m-%d %H:%M}',MARGIN,y+10,WIDTH-2*MARGIN,25,MUTED)
    y=text(f'{report.scene_id.removeprefix("group:")}  ·  {report.range.timezone}  ·  快照 {snapshot:%m-%d %H:%M:%S}',MARGIN,y+3,WIDTH-2*MARGIN,22,MUTED)
    if report.range.focus:y=text('关注：'+report.range.focus,MARGIN,y+12,WIDTH-2*MARGIN,25)
    y+=28
    card_width=(WIDTH-2*MARGIN-36)//4
    values=[('已保存消息',report.statistics.messages),('参与账号',report.statistics.participants),
        ('展示文本字符',report.statistics.characters),('已完成分析',report.coverage.analyzed_messages)]
    for index,(label,value) in enumerate(values):
        x=MARGIN+index*(card_width+12)
        rect((x,y,x+card_width,y+142),THEME.card)
        text(label,x+20,y+17,card_width-40,22,MUTED)
        text(f'{value:,}',x+20,y+61,card_width-40,38)
    y+=168
    status='已覆盖本范围保存的文本' if report.coverage.complete else '部分报告 · 仍有消息未分析'
    rect((MARGIN,y,WIDTH-MARGIN,y+134),THEME.soft)
    text(status,MARGIN+24,y+18,WIDTH-2*MARGIN-48,28)
    text(f'本次已读 {report.coverage.read_messages:,} 条  ·  复用分析 {report.coverage.reused_messages:,} 条  ·  尚未分析 {report.coverage.unfinished_messages:,} 条',
        MARGIN+24,y+66,WIDTH-2*MARGIN-48,22)
    y+=162

    y=text('发言时段',MARGIN,y,WIDTH-2*MARGIN,32)
    y=text('按业务时区汇总 0—23 时；跨日范围合并相同时段',MARGIN,y+2,WIDTH-2*MARGIN,22,MUTED)
    counts=Counter()
    for item in report.statistics.activity:counts[datetime.fromisoformat(item.start_at).astimezone(zone).hour]+=item.messages
    top=max(counts.values(),default=0)
    chart_y=y+18;chart_height=188;column=(WIDTH-2*MARGIN)/24
    for hour in range(24):
        x=MARGIN+hour*column
        height=round(counts[hour]/top*chart_height) if top else 0
        if height:rect((x+4,chart_y+chart_height-height,x+column-4,chart_y+chart_height),ACCENT,5)
        if hour%3==0:text(str(hour),x+4,chart_y+chart_height+10,column*2,18,MUTED)
    line((MARGIN,chart_y+chart_height,WIDTH-MARGIN,chart_y+chart_height),'#DACFD3')
    y=chart_y+chart_height+64
    if report.statistics.active_members:
        member=report.statistics.active_members[0]
        y=text(f'发言最多：{member.display_name}  ·  {member.messages:,} 条',MARGIN,y,WIDTH-2*MARGIN,25)
    y+=30

    names={member.actor_id:member.display_name for member in report.statistics.active_members}
    y=text('主要讨论',MARGIN,y,WIDTH-2*MARGIN,32)
    if not report.topics:y=text('本次没有提取到可采用的主要话题。',MARGIN,y+12,WIDTH-2*MARGIN,25,MUTED)
    for index,topic in enumerate(report.topics,1):
        top_y=y+18
        block_start=len(elements)
        bottom=text(f'{index:02d}  {topic.title}',MARGIN+26,top_y+22,WIDTH-2*MARGIN-52,32)
        bottom=text(topic.summary,MARGIN+26,bottom+10,WIDTH-2*MARGIN-52,28)
        people='、'.join(names[actor] for actor in topic.participant_ids if actor in names)
        bottom=text(f'{len(topic.source_event_ids)} 条来源消息'+('  ·  '+people if people else ''),
            MARGIN+26,bottom+12,WIDTH-2*MARGIN-52,22,MUTED)
        elements.insert(block_start,('rect',(MARGIN,top_y,WIDTH-MARGIN,bottom+24),THEME.card,THEME.radius_inner))
        y=bottom+36
    y+=20
    y=text('原话摘录',MARGIN,y,WIDTH-2*MARGIN,32)
    if not report.quotes:y=text('本次未选用金句，正文不由模型补写。',MARGIN,y+12,WIDTH-2*MARGIN,25,MUTED)
    for quote in report.quotes:
        top_y=y+18;block_start=len(elements)
        bottom=text(quote.text,MARGIN+30,top_y+22,WIDTH-2*MARGIN-60,28)
        bottom=text(f'{quote.display_name}  ·  {quote.sent_at.astimezone(zone):%m-%d %H:%M}',MARGIN+30,bottom+12,WIDTH-2*MARGIN-60,22,MUTED)
        if quote.comment:bottom=text(quote.comment,MARGIN+30,bottom+12,WIDTH-2*MARGIN-60,25,MUTED)
        elements.insert(block_start,('rect',(MARGIN,top_y,WIDTH-MARGIN,bottom+24),THEME.card,THEME.radius_inner))
        line((MARGIN+3,top_y+16,MARGIN+3,bottom+8),ACCENT,5)
        y=bottom+36
    if report.comment:
        y=text('简短点评',MARGIN,y+28,WIDTH-2*MARGIN,32)
        y=text(report.comment,MARGIN,y+12,WIDTH-2*MARGIN,28)
    if report.unresolved:
        y=text('覆盖与未确认内容',MARGIN,y+30,WIDTH-2*MARGIN,32)
        for issue in report.unresolved[:8]:y=text('· '+issue,MARGIN,y+10,WIDTH-2*MARGIN,25,MUTED)
        if len(report.unresolved)>8:y=text(f'另有 {len(report.unresolved)-8} 项保留在结构化报告中。',MARGIN,y+8,WIDTH-2*MARGIN,22,MUTED)
    y+=30
    line((MARGIN,y,WIDTH-MARGIN,y),'#DACFD3')
    y=text('统计来自固定快照中的已保存消息；话题与点评为派生分析。',MARGIN,y+20,WIDTH-2*MARGIN,22,MUTED)
    y=text('引语从原话的展示文本提取，保留原作者与来源；本报告未识别图片内容。',MARGIN,y+2,WIDTH-2*MARGIN,22,MUTED)
    if page_count > 1:
        y=text(f'第 {page_index} / {page_count} 页',WIDTH-MARGIN-180,y+12,180,20,MUTED)
    image=Image.new('RGB',(WIDTH,y+48),BACKGROUND)
    draw=ImageDraw.Draw(image)
    for operation in elements:
        if operation[0]=='rect':draw.rounded_rectangle(operation[1],radius=operation[3],fill=operation[2])
        elif operation[0]=='line':draw.line(operation[1],fill=operation[2],width=operation[3])
        else:draw.text(operation[1],operation[2],font=operation[3],fill=operation[4])
    output=BytesIO();image.save(output,format='PNG')
    return output.getvalue()


def render_report_pages(report: SingleGroupReport, font_path: Path) -> list[bytes]:
    """Return an ordered page collection while keeping the legacy single-page renderer.

    The first version of the template fits ordinary reports on one canvas.  Keeping
    this boundary as a list lets delivery and saved artifacts support semantic pages
    without changing the report JSON or rerunning analysis.
    """
    blocks = [('topic', item) for item in report.topics] + [('quote', item) for item in report.quotes]
    if not blocks:
        return [render_report(report, font_path)]

    def block_height(block):
        item = block[1]
        if block[0] == 'topic':
            return 180 + (len(item.title) + len(item.summary)) // 2
        return 120 + len(item.text) // 2 + len(item.comment)

    # Header, statistics and chart occupy the first part of every page. Keep
    # semantic blocks whole; an unusually large single block gets its own page
    # rather than being cropped or silently reduced.
    chunks = split_pages(blocks, 1500, block_height)
    page_count = len(chunks)
    pages = []
    for index, chunk in enumerate(chunks, 1):
        topics = [item for kind, item in chunk if kind == 'topic']
        quotes = [item for kind, item in chunk if kind == 'quote']
        page = report.model_copy(update={
            'topics': topics,
            'quotes': quotes,
            'comment': report.comment if index == page_count else '',
            'unresolved': report.unresolved if index == page_count else [],
        })
        pages.append(render_report(page, font_path, page_index=index, page_count=page_count))
    return pages
