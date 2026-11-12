"""Render the final measured Markdown with bundled python-docx; no H3 dependencies."""
import argparse,re
from pathlib import Path

def build(source,target):
    from docx import Document
    from docx.shared import Inches,Pt,RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.enum.table import WD_TABLE_ALIGNMENT,WD_CELL_VERTICAL_ALIGNMENT
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE
    doc=Document();sec=doc.sections[0]
    sec.page_width=Inches(8.5);sec.page_height=Inches(11)
    sec.top_margin=sec.bottom_margin=Inches(.8);sec.left_margin=sec.right_margin=Inches(1)
    sec.header_distance=sec.footer_distance=Inches(.35)
    for name in ('Normal','Title','Subtitle','Heading 1','Heading 2','Heading 3','List Bullet','List Number','Caption','Footer'):
        style=doc.styles[name];style.font.name='Arial';style.font.color.rgb=RGBColor(0,0,0)
        fonts=style._element.get_or_add_rPr().get_or_add_rFonts()
        for attr in ('asciiTheme','hAnsiTheme','eastAsiaTheme','cstheme'):
            fonts.attrib.pop(qn('w:'+attr),None)
        for attr,value in [('ascii','Arial'),('hAnsi','Arial'),('cs','Arial'),('eastAsia','Hiragino Sans GB')]:fonts.set(qn('w:'+attr),value)
        lang=style._element.get_or_add_rPr().find(qn('w:lang'))
        if lang is None:lang=OxmlElement('w:lang');style._element.get_or_add_rPr().append(lang)
        lang.set(qn('w:val'),'en-US');lang.set(qn('w:eastAsia'),'zh-CN')
    doc.styles['Caption'].font.italic=False;doc.styles['Caption'].font.size=Pt(10)
    normal=doc.styles['Normal'];normal.font.size=Pt(11)
    normal.paragraph_format.line_spacing=Pt(17.5);normal.paragraph_format.space_after=Pt(6)
    for name,size,before,after in [('Title',23,0,10),('Heading 1',16,17,7),('Heading 2',13,13,6),('Heading 3',11.5,10,5)]:
        st=doc.styles[name];st.font.size=Pt(size);st.font.bold=True;st.paragraph_format.space_before=Pt(before);st.paragraph_format.space_after=Pt(after);st.paragraph_format.keep_with_next=True;st.paragraph_format.line_spacing=Pt(size*1.3)
        for border in list(st._element.xpath('.//w:pBdr')):border.getparent().remove(border)
    def run_text(p,text,bold=False):
        parts=re.split(r'(\*\*.*?\*\*|`[^`]+`|\[[^\]]+\]\([^\)]+\))',text)
        for part in parts:
            if not part:continue
            match=re.fullmatch(r'\[([^\]]+)\]\(([^\)]+)\)',part)
            if match:
                hyperlink=OxmlElement('w:hyperlink');hyperlink.set(qn('r:id'),p.part.relate_to(match[2],RELATIONSHIP_TYPE.HYPERLINK,is_external=True))
                r=OxmlElement('w:r');pr=OxmlElement('w:rPr');color=OxmlElement('w:color');color.set(qn('w:val'),'1F4E79');pr.append(color);r.append(pr);t=OxmlElement('w:t');t.text=match[1];r.append(t);hyperlink.append(r);p._p.append(hyperlink);continue
            r=p.add_run(part[2:-2] if part.startswith('**') and part.endswith('**') else part[1:-1] if part.startswith('`') and part.endswith('`') else part)
            r.bold=True if bold or part.startswith('**') else None
            if part.startswith('`'):r.font.name='Menlo';r.font.size=Pt(9.2)
    def cell_style(cell,header=False,alternate=False,align='left'):
        cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER;pr=cell._tc.get_or_add_tcPr()
        borders=OxmlElement('w:tcBorders')
        for side in ('top','left','bottom','right'):
            edge=OxmlElement('w:'+side);edge.set(qn('w:val'),'single');edge.set(qn('w:sz'),'4');edge.set(qn('w:color'),'D9D9D9');borders.append(edge)
        pr.append(borders);margin=OxmlElement('w:tcMar')
        for side in ('top','left','bottom','right'):
            m=OxmlElement('w:'+side);m.set(qn('w:w'),'90' if side in ('top','bottom') else '105');m.set(qn('w:type'),'dxa');margin.append(m)
        pr.append(margin)
        if header or alternate:
            shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'203B5E' if header else 'EFF4F9');pr.append(shade)
        for p in cell.paragraphs:
            p.paragraph_format.space_before=Pt(0);p.paragraph_format.space_after=Pt(1);p.paragraph_format.line_spacing=Pt(14.5)
            p.alignment=WD_ALIGN_PARAGRAPH.CENTER if align=='center' else WD_ALIGN_PARAGRAPH.LEFT
            for r in p.runs:
                r.font.size=Pt(10);r.font.bold=header
                r.font.color.rgb=RGBColor(255,255,255) if header else RGBColor(0,0,0)
    def table(rows):
        n=len(rows[0]);widths={2:[1.55,4.95],3:[1.35,1.8,3.35],4:[1.3,1.1,1.6,2.5],5:[1.3,.65,1.3,1.35,1.9],6:[.9,.5,.8,1.05,1.05,2.2],7:[.7,.5,.75,.9,.9,1.35,1.4]}.get(n,[6.5/n]*n)
        if 'task ID' in rows[0]:widths=[.55,1.45,1.05,1.05,1.1,1.3]
        if rows[0][0]=='阶段与任务':widths=[1.65,.75,1.05,1.05,2.0]
        if rows[0][0]=='阶段' and '终态口径' in rows[0]:widths=[1.0,.75,.85,1.05,1.15,1.7]
        if rows[0][0]=='阶段' and '采样 n' in rows[0]:widths=[1.0,.7,1.1,1.0,1.2,1.5]
        if rows[0][0]=='批次':widths=[.9,.5,1.0,1.25,1.25,1.6]
        if rows[0][0]=='项目' and '金额 USD' in rows[0]:widths=[1.15,1.9,.85,2.6]
        if rows[0][0]=='实际规格组合':widths=[5.75,.75]
        if rows[0][0]=='需求利用率':widths=[1.1,3.4,2.0]
        t=doc.add_table(rows=len(rows),cols=n);t.alignment=WD_TABLE_ALIGNMENT.CENTER;t.autofit=False
        for col,w in zip(t.columns,widths):col.width=Inches(w)
        for i,row in enumerate(rows):
            tr=t.rows[i]._tr.get_or_add_trPr();cant=OxmlElement('w:cantSplit');tr.append(cant)
            if i==0:
                repeat=OxmlElement('w:tblHeader');repeat.set(qn('w:val'),'true');tr.append(repeat)
            for j,value in enumerate(row):
                cell=t.cell(i,j);cell.width=Inches(widths[j]);p=cell.paragraphs[0];run_text(p,value)
                is_numeric=bool(re.fullmatch(r'[\d.,/%$–—\- ±]+|通过|缺失|待评|N/A',value))
                cell_style(cell,header=i==0,alternate=i%2==0 and i>0,align='center' if i==0 or is_numeric else 'left')
        gap=doc.add_paragraph();gap.paragraph_format.space_after=Pt(3);gap.paragraph_format.space_before=Pt(0);gap.paragraph_format.line_spacing=Pt(2);gap.add_run().font.size=Pt(2)
    lines=source.read_text().splitlines();i=0;code=False
    while i<len(lines):
        text=lines[i].strip()
        if text.startswith('```'):code=not code;i+=1;continue
        if code:
            p=doc.add_paragraph();p.paragraph_format.line_spacing=1.08;p.paragraph_format.space_after=Pt(2);p.paragraph_format.left_indent=Inches(.08)
            r=p.add_run(lines[i]);r.font.name='Menlo';r.font.size=Pt(8.8);p.paragraph_format.keep_together=True;p.paragraph_format.keep_with_next=(i+1<len(lines) and not lines[i+1].strip().startswith('```'));i+=1;continue
        if not text:i+=1;continue
        if text.startswith('<!--'):i+=1;continue
        image=re.fullmatch(r'!\[([^\]]*)\]\(([^\)]+)\)',text)
        if image:
            img=Path(image[2]);img=img if img.is_absolute() else source.parent/img
            p=doc.add_paragraph();p.alignment=WD_ALIGN_PARAGRAPH.CENTER;p.paragraph_format.keep_with_next=True;p.paragraph_format.keep_together=True;p.paragraph_format.line_spacing=1.0;p.paragraph_format.space_after=Pt(0);shape=p.add_run().add_picture(str(img),width=Inches(6.2));shape._inline.docPr.set('descr',image[1])
            cap=doc.add_paragraph(image[1],style='Caption');cap.alignment=WD_ALIGN_PARAGRAPH.CENTER;cap.paragraph_format.space_after=Pt(9);i+=1;continue
        if text.startswith('|') and i+1<len(lines) and re.match(r'^\|[ :\-\|]+\|$',lines[i+1].strip()):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith('|'):
                raw=lines[i].strip();i+=1
                if re.match(r'^\|[ :\-\|]+\|$',raw):continue
                rows.append([v.strip() for v in raw.strip('|').split('|')])
            table(rows);continue
        heading=re.match(r'^(#{1,3})\s+(.*)',text)
        if heading:
            level=len(heading[1]);p=doc.add_paragraph(style='Title' if level==1 else 'Heading '+str(level-1));run_text(p,heading[2]);p.paragraph_format.page_break_before=heading[2]=='实验范围和方法';i+=1;continue
        bullet=re.match(r'^[-*]\s+(.*)',text);number=re.match(r'^\d+\.\s+(.*)',text)
        p=doc.add_paragraph(style='List Bullet' if bullet else 'List Number' if number else 'Normal');run_text(p,bullet[1] if bullet else number[1] if number else text)
        if text.startswith('表 '):p.paragraph_format.keep_with_next=True
        i+=1
    footer=sec.footer.paragraphs[0];footer.alignment=WD_ALIGN_PARAGRAPH.RIGHT
    r=footer.add_run('第 ');r.font.size=Pt(9)
    fld=OxmlElement('w:fldSimple');fld.set(qn('w:instr'),'PAGE');footer._p.append(fld)
    r=footer.add_run(' 页');r.font.size=Pt(9)
    doc.core_properties.title='MiniMax H3 八卡 RTX PRO 6000 实测报告';doc.core_properties.subject='T2VA 与首帧 FL2VA 自费 PoC';doc.core_properties.author='PoC 项目';doc.core_properties.comments=''
    target.parent.mkdir(parents=True,exist_ok=True);doc.save(target)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('markdown',type=Path);p.add_argument('docx',type=Path);a=p.parse_args();build(a.markdown,a.docx)
