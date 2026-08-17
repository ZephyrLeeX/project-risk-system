import { describe, expect, it } from "vitest";

import { renderAgentMarkdown } from "@/utils/agent-markdown";

describe("agent markdown renderer", () => {
  it("renders the supported Markdown structures", () => {
    const html = renderAgentMarkdown(
      "# 标题\n\n**粗体** 与 *斜体*\n\n- 一项\n- 二项\n\n| 名称 | 金额 |\n| --- | --- |\n| 风险 | 774,000元 |\n\n`代码`\n\n> 引用\n\n---",
    );

    expect(html).toContain("<h1>标题</h1>");
    expect(html).toContain("<strong>粗体</strong>");
    expect(html).toContain("<em>斜体</em>");
    expect(html).toContain("<ul>");
    expect(html).toContain('class="agent-markdown-table-wrap"');
    expect(html).toContain("<table>");
    expect(html).toContain("<code>代码</code>");
    expect(html).toContain("<blockquote>");
    expect(html).toContain("<hr>");
  });

  it("escapes raw HTML and permits only safe external links", () => {
    const html = renderAgentMarkdown(
      '<script>alert(1)</script> [安全](https://example.com) [危险](javascript:alert(1)) [数据](data:text/html,x)',
    );

    expect(html).toContain("&lt;script&gt;");
    expect(html).not.toContain("<script>");
    expect(html).toContain('href="https://example.com"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html).not.toContain('href="javascript:');
    expect(html).not.toContain('href="data:');
  });
});
