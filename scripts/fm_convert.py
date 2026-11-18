"""把用户投放的各种格式文件转成 Markdown。只用标准库；本机有 markitdown / pandoc / pdftotext / textutil 时优先借用。

convert(path) -> (markdown 文本或 None, 用的方法, 说明)
返回 None 表示脚本转不了，需要交给能直接读这类文件的智能体（PDF、图片等）。
"""
import csv, io, json, os, re, shutil, subprocess, zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

MAX_BYTES = 50 * 1024 * 1024
MAX_ROWS = 300
TEXT_EXT = {".txt", ".text", ".log", ".markdown", ".mdown", ".rst", ".org", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".bmp", ".tiff", ".svg"}
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".avi", ".mp3", ".wav", ".m4a", ".aac", ".flac"}
NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _run(cmd, timeout=120):
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.decode("utf-8", errors="ignore")
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


def _table(rows):
    rows = [[str(c).replace("|", "\\|").replace("\n", " ").strip() for c in r] for r in rows if any(str(c).strip() for c in r)]
    if not rows:
        return ""
    n = max(len(r) for r in rows)
    total = len(rows)
    rows = [r + [""] * (n - len(r)) for r in rows[:MAX_ROWS]]
    out = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * n]
    out += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    if total > MAX_ROWS:
        out.append(f"\n> ⚠ 表格已截断：共 {total} 行，只转了前 {MAX_ROWS} 行；完整内容见原件（.fm/originals/）。")
    return "\n".join(out)


def _read_text(p):
    raw = open(p, "rb").read()
    for enc in ("utf-8-sig", "gb18030", "utf-16", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="ignore")


def docx(p):
    with zipfile.ZipFile(p) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    body = root.find("w:body", NS)
    out = []

    def ptext(par):
        return "".join(t.text or "" for t in par.iter(f"{{{NS['w']}}}t")).strip()

    for el in list(body) if body is not None else []:
        tag = el.tag.split("}")[1]
        if tag == "p":
            t = ptext(el)
            if not t:
                continue
            st = el.find("w:pPr/w:pStyle", NS)
            style = st.get(f"{{{NS['w']}}}val", "") if st is not None else ""
            m = re.search(r"(?:Heading|标题)\s*(\d)", style)
            if m:
                out.append("#" * min(int(m.group(1)) + 1, 6) + " " + t)
            elif el.find("w:pPr/w:numPr", NS) is not None:
                out.append("- " + t)
            else:
                out.append(t)
        elif tag == "tbl":
            rows = [[" ".join(ptext(pp) for pp in tc.iter(f"{{{NS['w']}}}p")) for tc in tr.findall("w:tc", NS)] for tr in el.findall("w:tr", NS)]
            out.append(_table(rows))
    return "\n\n".join(x for x in out if x)


def pptx(p):
    out = []
    with zipfile.ZipFile(p) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for i, n in enumerate(slides, 1):
            root = ET.fromstring(z.read(n))
            paras = ["".join(t.text or "" for t in par.iter(f"{{{NS['a']}}}t")).strip() for par in root.iter(f"{{{NS['a']}}}p")]
            paras = [x for x in paras if x]
            if paras:
                out.append(f"## 第 {i} 页 / Slide {i}\n\n" + "\n".join("- " + x for x in paras))
    return "\n\n".join(out)


def xlsx(p):
    out = []
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", NS):
                shared.append("".join(t.text or "" for t in si.iter(f"{{{NS['s']}}}t")))
        titles = []
        if "xl/workbook.xml" in names:
            titles = [s.get("name") for s in ET.fromstring(z.read("xl/workbook.xml")).iter(f"{{{NS['s']}}}sheet")]
        sheets = sorted((n for n in names if re.match(r"xl/worksheets/sheet\d+\.xml$", n)), key=lambda n: int(re.findall(r"\d+", n)[-1]))
        for i, n in enumerate(sheets):
            rows = []
            for row in ET.fromstring(z.read(n)).iter(f"{{{NS['s']}}}row"):
                cells = []
                for c in row.findall("s:c", NS):
                    v = c.find("s:v", NS)
                    t = c.get("t")
                    if t == "s" and v is not None:
                        cells.append(shared[int(v.text)] if int(v.text) < len(shared) else "")
                    elif t == "inlineStr":
                        cells.append("".join(x.text or "" for x in c.iter(f"{{{NS['s']}}}t")))
                    else:
                        cells.append(v.text if v is not None else "")
                rows.append(cells)
                if len(rows) >= MAX_ROWS:
                    break
            title = titles[i] if i < len(titles) else f"Sheet{i + 1}"
            out.append(f"## {title}\n\n{_table(rows)}")
    return "\n\n".join(out)


class _HTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.out, self.skip, self.prefix = [], 0, ""

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif re.fullmatch(r"h[1-6]", tag):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag == "li":
            self.out.append("\n- ")
        elif tag in ("p", "div", "br", "tr", "section", "article"):
            self.out.append("\n")
        elif tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif tag in ("p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table"):
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip and data.strip():
            self.out.append(re.sub(r"\s+", " ", data))


def html(p):
    h = _HTML()
    h.feed(_read_text(p))
    return re.sub(r"\n{3,}", "\n\n", "".join(h.out)).strip()


def delimited(p, delim):
    rows = list(csv.reader(io.StringIO(_read_text(p)), delimiter=delim))
    return _table(rows)


def jsonfile(p):
    txt = _read_text(p)
    try:
        txt = json.dumps(json.loads(txt), ensure_ascii=False, indent=2)
    except ValueError:
        pass
    return "```json\n" + txt[:200000] + "\n```"


def convert(p):
    ext = os.path.splitext(p)[1].lower()
    if os.path.getsize(p) > MAX_BYTES:
        return None, "skip", "超过 50MB，不转换 / too large"
    if ext in MEDIA_EXT:
        return None, "skip", "音视频不转换，保留原文件 / media kept as is"
    if ext in IMAGE_EXT:
        return None, "agent", "图片：请能看图的智能体读取后写成 MD / needs a vision-capable agent"
    if shutil.which("markitdown") and ext not in TEXT_EXT:
        md = _run(["markitdown", p])
        if md:
            return md, "markitdown", ""
    try:
        if ext in TEXT_EXT:
            return _read_text(p), "text", ""
        if ext == ".docx":
            return docx(p), "docx", ""
        if ext == ".pptx":
            return pptx(p), "pptx", ""
        if ext in (".xlsx", ".xlsm"):
            return xlsx(p), "xlsx", ""
        if ext in (".html", ".htm", ".xhtml"):
            return html(p), "html", ""
        if ext == ".csv":
            return delimited(p, ","), "csv", ""
        if ext == ".tsv":
            return delimited(p, "\t"), "tsv", ""
        if ext in (".json", ".jsonl"):
            return jsonfile(p), "json", ""
    except (zipfile.BadZipFile, ET.ParseError, KeyError, ValueError) as e:
        return None, "error", f"解析失败：{e}"
    if ext == ".pdf":
        md = _run(["pdftotext", "-layout", p, "-"]) if shutil.which("pdftotext") else None
        if md:
            return md, "pdftotext", ""
        try:
            import pypdf  # 可选依赖
            return "\n\n".join((pg.extract_text() or "") for pg in pypdf.PdfReader(p).pages), "pypdf", ""
        except Exception:
            return None, "agent", "PDF：本机没有 pdftotext/pypdf，请能读 PDF 的智能体转换 / needs an agent that reads PDF"
    if shutil.which("textutil") and ext in (".doc", ".rtf", ".rtfd", ".odt", ".wordml", ".webarchive", ".pages"):
        md = _run(["textutil", "-convert", "txt", "-stdout", p])
        if md:
            return md, "textutil", ""
    if shutil.which("pandoc"):
        md = _run(["pandoc", p, "-t", "gfm"])
        if md:
            return md, "pandoc", ""
    return None, "agent", f"{ext or '无扩展名'}：脚本转不了，请智能体读取后写成 MD / needs an agent"
