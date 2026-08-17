import MarkdownIt from "markdown-it";

function isAllowedLink(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:";
  } catch {
    return false;
  }
}

const renderer = new MarkdownIt({
  breaks: true,
  html: false,
  linkify: false,
  typographer: false,
});

renderer.validateLink = isAllowedLink;
renderer.renderer.rules.link_open = (tokens, index, options, _env, self) => {
  const token = tokens[index];
  if (!token) return "";
  token.attrSet("target", "_blank");
  token.attrSet("rel", "noopener noreferrer");
  return self.renderToken(tokens, index, options);
};
renderer.renderer.rules.table_open = () =>
  '<div class="agent-markdown-table-wrap"><table>\n';
renderer.renderer.rules.table_close = () => "</table>\n</div>\n";

/**
 * Renders agent text with raw HTML disabled and an allowlist of safe link
 * protocols. It is the sole source permitted to produce HTML for agent output.
 */
export function renderAgentMarkdown(content: string): string {
  return renderer.render(content);
}
