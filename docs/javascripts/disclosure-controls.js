(() => {
  "use strict";

  const ARTICLE_SELECTOR = "article.md-content__inner";
  const CONTROL_CLASS = "disclosure-controls";
  const BUTTON_CLASS = "disclosure-controls__toggle";
  const CODE_DETAILS_CLASS = "code-disclosure";
  const EXCLUDED_CODE_SELECTOR = [
    ".arithmatex",
    "mjx-container",
    ".MathJax",
    ".katex",
    ".mermaid",
    "[data-mermaid]",
  ].join(", ");

  const languageLabels = {
    bash: "Shell",
    c: "C",
    cpp: "C++",
    "c++": "C++",
    css: "CSS",
    html: "HTML",
    javascript: "JavaScript",
    js: "JavaScript",
    json: "JSON",
    markdown: "Markdown",
    md: "Markdown",
    python: "Python",
    py: "Python",
    shell: "Shell",
    sh: "Shell",
    text: "文本 / 样例",
    txt: "文本 / 样例",
    yaml: "YAML",
    yml: "YAML",
  };

  function codeRoot(candidate) {
    return candidate.closest(".highlight") || candidate;
  }

  function isExcludedCodeBlock(block) {
    if (block.matches(EXCLUDED_CODE_SELECTOR)) return true;
    if (block.closest(EXCLUDED_CODE_SELECTOR)) return true;
    const code = block.matches("pre")
      ? block.querySelector(":scope > code")
      : block.querySelector("code");
    if (!code) return block.matches("pre");
    const classes = `${block.className || ""} ${code.className || ""}`;
    return /\blanguage-(?:arithmatex|math|mermaid)\b/i.test(classes);
  }

  function languageLabel(block) {
    const code = block.matches("pre")
      ? block.querySelector(":scope > code")
      : block.querySelector("code");
    const classes = [
      ...block.classList,
      ...(code ? [...code.classList] : []),
    ];
    const languageClass = classes.find((name) =>
      name.toLowerCase().startsWith("language-")
    );
    if (!languageClass) return "代码 / 示例";
    const language = languageClass.slice("language-".length).toLowerCase();
    return languageLabels[language] || language.toUpperCase();
  }

  function standaloneCodeBlocks(article) {
    const roots = new Set();
    article.querySelectorAll(".highlight, pre").forEach((candidate) => {
      const root = codeRoot(candidate);
      if (!article.contains(root)) return;
      if (root.closest("details")) return;
      if (isExcludedCodeBlock(root)) return;
      if (root.matches("pre") && !root.querySelector(":scope > code")) return;
      roots.add(root);
    });
    return [...roots];
  }

  function wrapStandaloneCodeBlocks(article) {
    standaloneCodeBlocks(article).forEach((block) => {
      const details = document.createElement("details");
      details.className = CODE_DETAILS_CLASS;
      details.dataset.disclosureGenerated = "true";

      const summary = document.createElement("summary");
      summary.textContent = languageLabel(block);
      details.append(summary);

      block.before(details);
      details.append(block);
    });
  }

  function insertControl(article) {
    const controls = document.createElement("div");
    controls.className = CONTROL_CLASS;

    const button = document.createElement("button");
    button.className = BUTTON_CLASS;
    button.type = "button";
    button.setAttribute("aria-expanded", "false");
    controls.append(button);

    const heading = article.querySelector(":scope > h1");
    if (heading) heading.insertAdjacentElement("afterend", controls);
    else article.prepend(controls);
    return button;
  }

  function enhanceArticle(article) {
    if (!article || article.dataset.disclosureEnhanced === "true") return;
    article.dataset.disclosureEnhanced = "true";

    wrapStandaloneCodeBlocks(article);

    const details = [...article.querySelectorAll("details")];
    details.forEach((item) => item.removeAttribute("open"));
    const button = insertControl(article);

    const updateButton = () => {
      const allOpen =
        details.length > 0 && details.every((item) => item.open);
      button.disabled = details.length === 0;
      button.setAttribute("aria-expanded", String(allOpen));
      if (details.length === 0) {
        button.textContent = "本页无折叠内容";
        button.setAttribute("aria-label", "本页没有可展开的折叠内容");
        return;
      }
      const action = allOpen ? "收起全部" : "展开全部";
      button.textContent = `${action}（${details.length}）`;
      button.setAttribute(
        "aria-label",
        `${action}：本页共 ${details.length} 个折叠区域`
      );
    };

    button.addEventListener("click", () => {
      const shouldOpen = !(
        details.length > 0 && details.every((item) => item.open)
      );
      details.forEach((item) => {
        item.open = shouldOpen;
      });
      updateButton();
    });

    article.addEventListener("toggle", updateButton, true);
    updateButton();
  }

  function enhanceCurrentArticle() {
    enhanceArticle(document.querySelector(ARTICLE_SELECTOR));
  }

  const materialDocument = globalThis.document$;
  if (materialDocument?.subscribe) {
    materialDocument.subscribe(enhanceCurrentArticle);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", enhanceCurrentArticle, {
      once: true,
    });
  } else {
    enhanceCurrentArticle();
  }
})();
