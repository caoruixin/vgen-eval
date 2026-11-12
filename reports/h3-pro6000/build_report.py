"""Add an architecture chapter to the archived report without changing the archive.

Run with the bundled document Python runtime. Figures use Pillow and SVG, with
no model calls, downloaded weights, or new benchmark measurements.
"""
from __future__ import annotations

import base64
import hashlib
import html
import importlib.util
import json
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ARCHIVE = ROOT / "h3-pro6000-poc/results"
ORIGINAL = ARCHIVE / "reports/final"
STEM = "MiniMax_H3_PRO6000_8GPU_PoC_Report"
OUT = HERE / "architecture-v1"
FIGURES = OUT / "figures"
EXPERIMENT = "h3-pro6000-2026-11-07"
REVISION = "architecture-2026-11-08"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Figure:
    """Draw the same geometry into a high-resolution PNG and an editable SVG."""

    def __init__(self, width: int, height: int, title: str):
        self.width, self.height, self.scale = width, height, 2
        self.image = Image.new("RGB", (width * self.scale, height * self.scale), "white")
        self.draw = ImageDraw.Draw(self.image)
        self.svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
                    f"<title>{html.escape(title)}</title>", '<rect width="100%" height="100%" fill="white"/>']
        self.text(50, 22, title, 38, bold=True)

    def text(self, x, y, value, size=29, color="#1d2631", bold=False, center=False):
        font = ImageFont.truetype(FONT, size * self.scale)
        lines = value.split("\n")
        for i, line in enumerate(lines):
            yy = y + i * size * 1.45
            xx = x - self.draw.textlength(line, font=font) / self.scale / 2 if center else x
            # Keep figure labels inside the page; overflow is an authoring error.
            if xx < 4 or xx + self.draw.textlength(line, font=font) / self.scale > self.width - 4:
                raise ValueError(f"figure label overflows: {line}")
            self.draw.text((xx * self.scale, yy * self.scale), line, fill=color, font=font,
                           stroke_width=1 if bold else 0)
            weight = ' font-weight="600"' if bold else ""
            self.svg.append(f'<text x="{xx:.1f}" y="{yy + size:.1f}" font-family="Hiragino Sans GB, Noto Sans CJK SC, sans-serif" font-size="{size}" fill="{color}"{weight}>{html.escape(line)}</text>')

    def box(self, x, y, w, h, title, body="", fill="#f1f5fa", border="#8da1ba", title_size=32):
        for value, size in ((title,title_size),(body,27)):
            font=ImageFont.truetype(FONT,size*self.scale)
            if any(self.draw.textlength(line,font=font)/self.scale>w-44 for line in value.split("\n")):
                raise ValueError(f"label exceeds its box: {value}")
        if body and 62+(len(body.split("\n"))-1)*27*1.45+33>h-8:
            raise ValueError(f"label exceeds box height: {body}")
        self.draw.rounded_rectangle(tuple(int(v * self.scale) for v in (x, y, x+w, y+h)),
                                    radius=14*self.scale, fill=fill, outline=border, width=2*self.scale)
        self.svg.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{border}" stroke-width="2"/>')
        self.text(x+22, y+15, title, title_size, bold=True)
        if body:
            self.text(x+22, y+62, body, 27)

    def arrow(self, points, color="#536b85", both=False):
        coords = [(int(x*self.scale), int(y*self.scale)) for x,y in points]
        self.draw.line(coords, fill=color, width=3*self.scale)
        self.svg.append(f'<polyline points="{" ".join(f"{x},{y}" for x,y in points)}" fill="none" stroke="{color}" stroke-width="3"/>')
        def head(a,b):
            import math
            angle=math.atan2(b[1]-a[1],b[0]-a[0])
            triangle=[b, (b[0]-13*math.cos(angle-.45),b[1]-13*math.sin(angle-.45)),
                      (b[0]-13*math.cos(angle+.45),b[1]-13*math.sin(angle+.45))]
            self.draw.polygon([(int(x*self.scale),int(y*self.scale)) for x,y in triangle],fill=color)
            self.svg.append(f'<polygon points="{" ".join(f"{x:.1f},{y:.1f}" for x,y in triangle)}" fill="{color}"/>')
        head(points[-2],points[-1])
        if both: head(points[1],points[0])

    def save(self, name):
        self.image.save(FIGURES / f"{name}.png", optimize=True)
        (FIGURES / f"{name}.svg").write_text("\n".join([*self.svg,"</svg>"]),encoding="utf-8")


def figures():
    f=Figure(1240,1490,"H3-Base 一次请求的推理链路")
    f.text(50,79,"本次 PoC 实际路径  T2VA 文生视频与仅首帧 FL2VA",27,color="#526276")
    f.box(50,132,1140,122,"1  请求与输入校验","冻结 prompt / 可选首帧 PNG / seed / 画幅与时长 / 推理步数")
    f.arrow([(620,254),(620,283),(325,283),(325,312)])
    f.arrow([(620,254),(620,283),(915,283),(915,312)])
    f.box(50,312,550,158,"2a  H3-Encoder","Qwen3-VL 编码文本与首帧语义\nT2VA 无首帧时只编码文本",title_size=31)
    f.box(640,312,550,158,"2b  首帧 VisualVAE 编码","生成视觉 latent 条件\n仅 FL2VA 使用此路径",fill="#f3f6f3",title_size=31)
    f.arrow([(325,470),(325,496),(620,496),(620,524)])
    f.arrow([(915,470),(915,496),(620,496),(620,524)])
    f.box(50,524,1140,122,"3  多模态序列与噪声初始化","条件 token + 视频与音频 latent 噪声 + 空间时间位置")
    f.arrow([(620,646),(620,697)])
    f.box(50,697,1140,187,"4  H3-Omni-Transformer 联合去噪","共同预测视频与音频更新，两个调度器分别推进\n保留首帧条件；循环 50 次基线或 8 次配置试验\n8 步已观察到，Turbo 适配器实际激活未证实",fill="#eaf1fb",border="#527aab")
    f.arrow([(620,884),(620,911),(325,911),(325,940)])
    f.arrow([(620,884),(620,911),(915,911),(915,940)])
    f.box(50,940,550,139,"5a  VisualVAE 解码","视频 latent → 像素帧")
    f.box(640,940,550,139,"5b  AudioVAE 解码","音频 latent → 立体声波形")
    f.arrow([(325,1079),(325,1106),(620,1106),(620,1135)])
    f.arrow([(915,1079),(915,1106),(620,1106),(620,1135)])
    f.box(50,1135,1140,122,"6  音视频编码与 MP4 封装","本轮输出：1344×768 / 24 fps / H.264 + 32 kHz 双声道 AAC")
    f.arrow([(620,1257),(620,1305)])
    f.box(50,1305,1140,122,"7  下载与技术校验","MP4 下载完成 → 主 E2E 结束；随后 ffprobe / 全文件解码 / 哈希")
    f.text(50,1450,"逻辑流程示意，不表示各模块实测耗时或 GPU/CPU 的完整任务调度。",25,color="#526276")
    f.save("h3-inference-flow")

    f=Figure(1240,878,"同一组八张 PRO6000 分阶段协作")
    f.text(50,80,"启动配置与 rank 布局示意；所有阶段复用相同的八张 GPU",27,color="#526276")
    f.box(50,142,1140,118,"文本与视觉语义编码  TP8","Qwen3-VL 的张量计算分布在 GPU 0 至 7")
    f.arrow([(620,260),(620,301)])
    f.box(50,301,550,210,"生成主干  NUMA 节点 0","TP4 分组：rank / GPU 0  1  2  3\n同节点内切分层的张量计算",title_size=30)
    f.box(640,301,550,210,"生成主干  NUMA 节点 1","TP4 分组：rank / GPU 4  5  6  7\n同节点内切分层的张量计算",title_size=30)
    f.text(84,451,"TP4",31,bold=True)
    f.text(1050,451,"TP4",31,bold=True)
    f.arrow([(210,477),(1030,477)],both=True)
    f.text(620,439,"Ulysses2 跨分组序列并行",27,center=True)
    f.text(50,534,"TP4 × Ulysses2 × Ring1 = 8 ranks  ·  共同完成一次生成",29,bold=True)
    f.arrow([(620,578),(620,617)])
    f.box(50,617,1140,122,"视频 VAE  tile 模式与 patch parallel size 8","按瓦片分配工作；实际并行程度受瓦片数量和运行分支影响")
    f.text(50,776,"×8 是 GPU 数量；C2 / C4 是客户端同时存在的请求数。",29,bold=True)
    f.text(50,822,"未比较其它 rank 映射，也未证明各阶段满载或此拓扑最优。",27,color="#526276")
    f.save("h3-gpu-layout")

    f=Figure(1240,865,"耗时测量边界与缺失的模块计时")
    f.text(50,80,"以下为口径示意，不按持续时间比例绘制",27,color="#526276")
    f.text(50,142,"自部署客户端 E2E  下载到推理服务器",32,bold=True)
    f.box(50,196,330,143,"请求与调度","校验与登记\nqueue_wait_ms",title_size=28)
    f.box(450,196,350,143,"H3-Base 服务阶段","管线整体计时\nstage_0_gen_ms",title_size=28)
    f.box(870,196,320,143,"输出与本地下载","MP4 完整下载\nE2E 在此结束",title_size=28)
    f.arrow([(380,265),(450,265)]);f.arrow([(800,265),(870,265)])
    f.text(50,377,"官方 API 客户端 E2E  下载到笔记本",32,bold=True)
    f.box(50,431,330,143,"公网请求与任务状态","先保存任务 ID\n轮询带来终态发现延迟",title_size=28)
    f.box(450,431,350,143,"官方内部服务","硬件 / 步数 / 模块\n内部实现与计时不可见",fill="#f3f3f3",title_size=28)
    f.box(870,431,320,143,"CDN 与公网下载","MP4 完整下载\nE2E 在此结束",title_size=28)
    f.arrow([(380,500),(450,500)]);f.arrow([(800,500),(870,500)])
    f.box(50,632,1140,122,"独立组件耗时保持缺失","未取得 Encoder / Denoiser / VAE / 封装的完整独立计时")
    f.text(50,790,"ffprobe 与完整解码校验在下载后执行，未计入主 E2E。",29,bold=True)
    f.text(50,834,"不能将两方 E2E 之差解释为纯模型速度差，也不按总耗时分摊模块时间。",25,color="#526276")
    f.save("h3-timing-scope")


def inline(text: str) -> str:
    parts=re.split(r"(\*\*.*?\*\*|`[^`]+`|\[[^\]]+\]\([^\)]+\))",text)
    out=[]
    for p in parts:
        link=re.fullmatch(r"\[([^\]]+)\]\(([^\)]+)\)",p)
        if link and link[2].startswith(("https://","http://")):
            out.append(f'<a href="{html.escape(link[2],quote=True)}">{html.escape(link[1])}</a>')
        elif p.startswith("**") and p.endswith("**"):
            out.append("<strong>"+html.escape(p[2:-2])+"</strong>")
        elif p.startswith("`") and p.endswith("`"):
            out.append("<code>"+html.escape(p[1:-1])+"</code>")
        else: out.append(html.escape(p))
    return "".join(out)


def reader(markdown: str) -> str:
    lines=markdown.splitlines();chunks=[];toc=[];i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line: i+=1;continue
        if line.startswith("```"):
            code=[];i+=1
            while i<len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i]);i+=1
            chunks.append("<pre><code>"+html.escape("\n".join(code))+"</code></pre>")
            i+=1;continue
        heading=re.match(r"^(#{1,3})\s+(.*)",line)
        if heading:
            level=len(heading[1]);anchor=f"section-{len(toc)}"
            if heading[2]=="H3 推理架构与各阶段说明":anchor="h3-inference-architecture"
            if level==2:toc.append((anchor,heading[2]))
            else:anchor=f"heading-{i}"
            chunks.append(f'<h{level} id="{anchor}">{inline(heading[2])}</h{level}>')
            i+=1;continue
        image=re.fullmatch(r"!\[([^\]]*)\]\(([^\)]+)\)",line)
        if image:
            path=(OUT/image[2]).resolve()
            if not path.is_relative_to(OUT):raise ValueError("image outside report")
            data=base64.b64encode(path.read_bytes()).decode()
            chunks.append(f'<figure><img src="data:image/png;base64,{data}" alt="{html.escape(image[1],quote=True)}"/><figcaption>{html.escape(image[1])}</figcaption></figure>')
            i+=1;continue
        if line.startswith("|") and i+1<len(lines) and re.fullmatch(r"[| :\-]+",lines[i+1].strip()):
            rows=[]
            while i<len(lines) and lines[i].strip().startswith("|"):
                row=lines[i].strip();i+=1
                if re.fullmatch(r"[| :\-]+",row):continue
                rows.append([inline(v.strip()) for v in row.strip("|").split("|")])
            chunks.append('<div class="table-wrap"><table><thead><tr>'+"".join("<th>"+v+"</th>" for v in rows[0])+"</tr></thead><tbody>"+
                          "".join("<tr>"+"".join("<td>"+v+"</td>" for v in row)+"</tr>" for row in rows[1:])+"</tbody></table></div>")
            continue
        if line.startswith("<!--"):i+=1;continue
        chunks.append("<p>"+inline(line)+"</p>");i+=1
    nav="".join(f'<a href="#{anchor}">{html.escape(title)}</a>' for anchor,title in toc)
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>PRO6000 PoC 报告与 H3 推理架构</title><style>
:root{{color-scheme:light dark;--paper:#fff;--ink:#17212b;--muted:#5e6977;--line:#dce2e9;--soft:#f3f6fa;--link:#245c96}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.85 -apple-system,BlinkMacSystemFont,"Hiragino Sans GB","Microsoft YaHei",sans-serif}}
main{{max-width:1040px;margin:auto;padding:28px 32px 80px}}a{{color:var(--link);overflow-wrap:anywhere}}h1,h2,h3{{color:var(--ink);line-height:1.45;scroll-margin-top:22px}}h1{{font-size:30px}}h2{{margin-top:48px;font-size:24px}}h3{{margin-top:30px;font-size:19px}}p{{margin:14px 0;overflow-wrap:anywhere}}.actions{{display:flex;flex-wrap:wrap;gap:12px;font-size:14px}}.actions a{{padding:6px 12px;border:1px solid var(--line);border-radius:7px;text-decoration:none}}.note{{color:var(--muted);font-size:14px}}details{{padding:14px 18px;border:1px solid var(--line);border-radius:9px;margin:24px 0}}summary{{cursor:pointer;font-weight:600}}nav{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 24px;margin-top:14px;font-size:14px}}figure{{margin:28px 0}}figure img{{display:block;width:100%;height:auto;border:1px solid var(--line);border-radius:8px;background:white}}figcaption{{font-size:14px;text-align:center;color:var(--muted);margin-top:9px}}.table-wrap{{overflow:auto;max-width:100%;margin:20px 0}}table{{border-collapse:collapse;width:100%;font-size:14px;line-height:1.7}}th,td{{border:1px solid var(--line);padding:10px 12px;text-align:left;vertical-align:top}}th{{background:var(--soft)}}code{{font:13px/1.65 ui-monospace,Menlo,monospace;overflow-wrap:anywhere}}pre{{overflow:auto;padding:16px;background:var(--soft);border:1px solid var(--line);border-radius:7px;line-height:1.5}}pre code{{overflow-wrap:normal;white-space:pre}}@media(max-width:600px){{main{{padding:18px 18px 60px}}h1{{font-size:25px}}h2{{font-size:21px}}nav{{grid-template-columns:1fr}}table{{min-width:550px}}}}@media(prefers-color-scheme:dark){{:root{{--paper:#151719;--ink:#e9ebed;--muted:#adb5bf;--line:#3a414a;--soft:#232830;--link:#a4c9f3}}}}
@media print{{.actions,details{{display:none}}main{{padding:0;max-width:none}}figure,table{{break-inside:avoid}}}}
</style></head><body><main><div class="actions"><a href="/benchmarks?experiment={EXPERIMENT}">← H3 Benchmark</a><a href="./report.docx">下载 Word 报告</a><a href="./report.md">下载 Markdown</a></div><p class="note">实验日期 2026-11-07 · 架构说明修订 2026-11-08 · 原始实验数据和归档保持不变</p><details open><summary>报告目录</summary><nav>{nav}</nav></details>{''.join(chunks)}</main></body></html>'''


def main():
    FIGURES.mkdir(parents=True,exist_ok=True)
    for source in (ORIGINAL/"figures").iterdir():
        if source.is_file():shutil.copyfile(source,FIGURES/source.name)
    figures()
    original=(ORIGINAL/f"{STEM}.md").read_text()
    section=(HERE/"architecture.md").read_text()
    assert original.count("## 统计和质量口径")==1
    text=original.replace("## 统计和质量口径",section.rstrip()+"\n\n## 统计和质量口径")
    text=text.replace("实测日期：2026年11月7日。","实测日期：2026年11月7日。架构说明修订：2026年11月8日；实验样本与统计结论不变。",1)
    markdown=OUT/"report.md";markdown.write_text(text,encoding="utf-8")
    spec=importlib.util.spec_from_file_location("original_report_converter",ARCHIVE/"reports/md_to_docx.py")
    converter=importlib.util.module_from_spec(spec);spec.loader.exec_module(converter)
    converter.build(markdown,OUT/"report.docx")
    (OUT/"report.html").write_text(reader(text),encoding="utf-8")
    evidence_paths=["config/baseline.json","runs/self/environment/runtime-contract.json",
        "runs/self/environment/rank-layout.txt","runs/self/environment/stage-metric-source.json",
        "runs/self/environment/turbo-activation-review.json","reports/data/all-request-attempts.csv"]
    provenance={"experiment_id":EXPERIMENT,"revision":REVISION,"measured_date":"2026-11-07","revision_date":"2026-11-08",
        "change":"Add H3-Base inference, eight-GPU mapping, measurement boundaries and experiment-stage glossary",
        "no_new_generation":True,"no_new_benchmark_samples":True,"turbo_activation_verified":False,
        "model_commit":"42ed227ee7df40d41602854ae760620d6eb651fe",
        "runtime_commit":"5917730a4b02aedb44d374173cdc1b10484ccd52",
        "evidence":[{"archive_path":p,"sha256":sha(ARCHIVE/p)} for p in evidence_paths]}
    (OUT/"provenance.json").write_text(json.dumps(provenance,ensure_ascii=False,indent=2)+"\n")
    manifest={"schema_version":1,"experiment_id":EXPERIMENT,"revision":REVISION,
        "source_artifacts":{f"report.{ext}":sha(ORIGINAL/f"{STEM}.{ext}") for ext in ("md","docx")},
        "files":[{"path":str(p.relative_to(OUT)),"size":p.stat().st_size,"sha256":sha(p)}
            for p in sorted(OUT.rglob("*")) if p.is_file() and p.name!="revision.json"]}
    (OUT/"revision.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"output":str(OUT),"revision":REVISION,"files":len(manifest["files"])},ensure_ascii=False))


if __name__=="__main__":main()
