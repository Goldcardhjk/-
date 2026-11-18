// 进步壹号 Agent 操作手册 → docx
// 用法：node build_docx.js
const fs = require("fs");
const path = require("path");
const M = require("module");
process.env.NODE_PATH = require("child_process")
  .execSync("npm root -g").toString().trim();
M.Module._initPaths();

const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell, ImageRun,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType,
  ShadingType, VerticalAlign, TableOfContents, PageBreak, Header, Footer,
  PageNumber,
} = require("docx");

const HERE = __dirname;
const SHOTS = path.join(HERE, "shots");
const OUT = path.join(HERE, "进步壹号详细介绍与操作手册.docx");

const BLUE = "1F4E79";
const GREY = "444444";
const bd = { style: BorderStyle.SINGLE, size: 1, color: "BFBFBF" };
const CB = { top: bd, bottom: bd, left: bd, right: bd };

const P = (text, opt = {}) =>
  new Paragraph({
    spacing: { after: opt.after === undefined ? 120 : opt.after },
    alignment: opt.align,
    indent: opt.indent,
    children: [new TextRun({
      text, bold: opt.bold, italics: opt.italics, size: opt.size || 21,
      color: opt.color || "000000", font: opt.mono ? "Consolas" : undefined,
    })],
  });

// 行内加粗：用 **…** 切分
const RICH = (text, opt = {}) => {
  const runs = [];
  text.split(/(\*\*[^*]+\*\*)/).forEach((seg) => {
    if (!seg) return;
    const b = seg.startsWith("**") && seg.endsWith("**");
    runs.push(new TextRun({
      text: b ? seg.slice(2, -2) : seg,
      bold: b || opt.bold,
      size: opt.size || 21,
      color: opt.color || "000000",
    }));
  });
  return new Paragraph({
    spacing: { after: opt.after === undefined ? 120 : opt.after },
    indent: opt.indent,
    alignment: opt.align,
    children: runs,
  });
};

const H = (text, lv) =>
  new Paragraph({ heading: lv, children: [new TextRun(text)] });

const BUL = (text) =>
  new Paragraph({
    numbering: { reference: "bl", level: 0 },
    spacing: { after: 80 },
    children: [new TextRun({ text, size: 21 })],
  });

// 带行内加粗的项目符号
const BULR = (text) => {
  const runs = [];
  text.split(/(\*\*[^*]+\*\*)/).forEach((seg) => {
    if (!seg) return;
    const b = seg.startsWith("**") && seg.endsWith("**");
    runs.push(new TextRun({
      text: b ? seg.slice(2, -2) : seg, bold: b, size: 21,
    }));
  });
  return new Paragraph({
    numbering: { reference: "bl", level: 0 },
    spacing: { after: 80 },
    children: runs,
  });
};

const NUM = (text, ref) => {
  const runs = [];
  text.split(/(\*\*[^*]+\*\*)/).forEach((seg) => {
    if (!seg) return;
    const b = seg.startsWith("**") && seg.endsWith("**");
    runs.push(new TextRun({
      text: b ? seg.slice(2, -2) : seg, bold: b, size: 21,
    }));
  });
  return new Paragraph({
    numbering: { reference: ref, level: 0 },
    spacing: { after: 80 },
    children: runs,
  });
};

// 引用块（灰底）
const QUOTE = (lines, fill) =>
  new Table({
    columnWidths: [9360],
    margins: { top: 120, bottom: 120, left: 200, right: 200 },
    rows: [new TableRow({
      children: [new TableCell({
        borders: CB,
        width: { size: 9360, type: WidthType.DXA },
        shading: { fill: fill || "F2F5F9", type: ShadingType.CLEAR },
        children: lines.map((l) => {
          const runs = [];
          String(l).split(/(\*\*[^*]+\*\*)/).forEach((seg) => {
            if (!seg) return;
            const b = seg.startsWith("**") && seg.endsWith("**");
            runs.push(new TextRun({
              text: b ? seg.slice(2, -2) : seg, bold: b, size: 20,
              color: GREY,
            }));
          });
          return new Paragraph({ spacing: { after: 60 }, children: runs });
        }),
      })],
    })],
  });

// 等宽代码块
const CODE = (lines) =>
  new Table({
    columnWidths: [9360],
    margins: { top: 120, bottom: 120, left: 200, right: 200 },
    rows: [new TableRow({
      children: [new TableCell({
        borders: CB,
        width: { size: 9360, type: WidthType.DXA },
        shading: { fill: "F6F8FA", type: ShadingType.CLEAR },
        children: lines.map((l) => new Paragraph({
          spacing: { after: 20 },
          children: [new TextRun({ text: l || " ", font: "Consolas", size: 19 })],
        })),
      })],
    })],
  });

// 表格：rows[0] 为表头
const TBL = (rows, widths) => {
  const w = widths || rows[0].map(() => Math.floor(9360 / rows[0].length));
  return new Table({
    columnWidths: w,
    margins: { top: 80, bottom: 80, left: 140, right: 140 },
    rows: rows.map((cells, ri) => new TableRow({
      tableHeader: ri === 0,
      children: cells.map((txt, ci) => new TableCell({
        borders: CB,
        width: { size: w[ci], type: WidthType.DXA },
        shading: ri === 0
          ? { fill: "DCE6F1", type: ShadingType.CLEAR }
          : { fill: "FFFFFF", type: ShadingType.CLEAR },
        verticalAlign: VerticalAlign.CENTER,
        children: [(() => {
          const runs = [];
          String(txt).split(/(\*\*[^*]+\*\*)/).forEach((seg) => {
            if (!seg) return;
            const b = seg.startsWith("**") && seg.endsWith("**");
            runs.push(new TextRun({
              text: b ? seg.slice(2, -2) : seg,
              bold: b || ri === 0, size: 19,
            }));
          });
          if (!runs.length) runs.push(new TextRun({ text: " ", size: 19 }));
          return new Paragraph({ children: runs });
        })()],
      })),
    })),
  });
};

// 配图 + figure 题注；按原图比例缩放到给定宽度，并限制最大高度
function IMG(file, caption, maxW, maxH) {
  const p = path.join(SHOTS, file);
  if (!fs.existsSync(p)) {
    console.error("!! 缺图", file);
    return [P("（配图缺失：" + file + "）", { italics: true, color: "C00000" })];
  }
  const buf = fs.readFileSync(p);
  // PNG 头里读真实像素尺寸：IHDR 宽高在 16..24 字节
  const pw = buf.readUInt32BE(16);
  const ph = buf.readUInt32BE(20);
  let w = Math.min(maxW || 600, pw);
  let h = Math.round(ph * (w / pw));
  // 竖长图（如拉高后的资料卡）按高度再收一次，否则一张图占满整页、题注被挤走。
  // 上限 720px ≈ 7.5in，正文页可用高度约 9.2in。
  const HMAX = maxH || 720;
  if (h > HMAX) {
    w = Math.round(w * (HMAX / h));
    h = HMAX;
  }
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { before: 120, after: 60 },
      children: [new ImageRun({
        type: "png", data: buf,
        transformation: { width: w, height: h },
        altText: { title: caption, description: caption, name: file },
      })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER,
      spacing: { after: 200 },
      children: [new TextRun({
        text: caption, size: 18, italics: true, color: GREY,
      })],
    }),
  ];
}

module.exports = {
  fs, path, Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  AlignmentType, LevelFormat, HeadingLevel, BorderStyle, WidthType,
  ShadingType, TableOfContents, PageBreak, Header, Footer, PageNumber,
  P, RICH, H, BUL, BULR, NUM, QUOTE, CODE, TBL, IMG, OUT, BLUE, GREY,
};
