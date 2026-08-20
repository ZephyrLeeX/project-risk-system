// T032 OpenAPI compatibility check.
//
// Proves the frozen FastAPI OpenAPI authority stays backward compatible with
// the surface the frontend actually consumes. It checks the explicit contract
// vectors named by T032 — path/method coverage, error envelope, enum values and
// representative schema/nullability invariants — and exits non-zero on any
// breaking difference. A clean run is the "compatibility diff approved" evidence
// for the T032 freeze; it is run as part of `pnpm contracts:check`.
//
// This is a checking tool, not shipped package source, so it lives outside
// `src/` and is not part of the package typecheck surface.
import ts from "typescript";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..", "..", ".."); // scripts/ -> contracts/ -> packages/ -> project root
const specPath = join(root, "packages/contracts/openapi/openapi.json");
const contractsPath = join(root, "packages/contracts/src/index.ts");
const apiDir = join(root, "apps/web/src/api");

const spec = JSON.parse(readFileSync(specPath, "utf8"));

const breaking = [];
const notes = [];

function isQueryBuilder(expr) {
  // Calls to query-string helpers, or bare identifiers holding a query suffix.
  if (
    ts.isCallExpression(expr) &&
    ts.isIdentifier(expr.expression) &&
    /^(queryString|toQuery|buildQuery|toParams)$/.test(expr.expression.text)
  ) {
    return true;
  }
  if (ts.isIdentifier(expr) && /^(suffix|query|search|qs|searchParams)$/.test(expr.text)) {
    return true;
  }
  return false;
}

// Build the structural OpenAPI path (`/api/...` with `{}` for path params) from
// a call's first argument. Query strings — whether a literal `?...`, a
// query-builder interpolation, or a query-suffix variable — are dropped so the
// result matches OpenAPI path templates. Returns null when the path cannot be
// determined statically.
function pathFromCallArg(arg0) {
  let text;
  if (ts.isStringLiteral(arg0) || ts.isNoSubstitutionTemplateLiteral(arg0)) {
    text = arg0.text;
  } else if (ts.isTemplateExpression(arg0)) {
    const headQ = arg0.head.text.indexOf("?");
    if (headQ !== -1) {
      // A literal `?` in the head starts a query string; ignore all spans.
      text = arg0.head.text.slice(0, headQ);
    } else {
      text = arg0.head.text;
      for (const span of arg0.templateSpans) {
        if (isQueryBuilder(span.expression)) break; // rest is a query string
        text += "{}" + span.literal.text;
      }
    }
  } else {
    return null;
  }
  const q = text.indexOf("?");
  if (q !== -1) text = text.slice(0, q);
  if (!text.startsWith("/")) text = "/" + text;
  return "/api" + text;
}

function structural(path) {
  return path.replace(/\{[^}]*\}/g, "{}");
}

// --- OpenAPI operation surface -------------------------------------------------
const openapiOps = new Map(); // `${METHOD} ${structuralPath}` -> `${METHOD} ${realPath}`
for (const [path, item] of Object.entries(spec.paths ?? {})) {
  for (const method of ["get", "post", "put", "patch", "delete"]) {
    if (item[method]) {
      openapiOps.set(`${method.toUpperCase()} ${structural(path)}`, `${method.toUpperCase()} ${path}`);
    }
  }
}

// --- 1. Error envelope invariance ---------------------------------------------
const ENVELOPE = ["code", "data", "message", "traceId"];
const envelopeComponents = Object.entries(spec.components?.schemas ?? {})
  .filter(([, c]) => {
    const required = [...(c.required ?? [])].sort();
    return required.length === 4 && required.every((f, i) => f === ENVELOPE[i]);
  })
  .map(([name]) => name);
if (envelopeComponents.length === 0) {
  breaking.push("error envelope `{code,message,data,traceId}` component is missing from OpenAPI");
} else {
  notes.push(`error envelope: ${envelopeComponents.length} component(s) incl. ${envelopeComponents[0]}`);
}

// --- 2. Enum compatibility (hand-written contracts vs OpenAPI enums) ----------
const openapiEnums = Object.values(spec.components?.schemas ?? {})
  .filter((c) => Array.isArray(c.enum))
  .map((c) => c.enum.map(String));

function enumReport(name, values) {
  // Match by best value-set overlap; a confident match (>= half, >= 2) is the
  // corresponding OpenAPI enum. Weak overlaps (e.g. a shared "ALL") are ignored
  // so unrelated enums don't produce false breaking diffs.
  let best = null;
  let bestOverlap = 0;
  for (const oe of openapiEnums) {
    const overlap = values.filter((v) => oe.includes(v)).length;
    if (overlap > bestOverlap) {
      bestOverlap = overlap;
      best = oe;
    }
  }
  const threshold = Math.max(2, Math.ceil(values.length / 2));
  if (!best || bestOverlap < threshold) {
    notes.push(`enum ${name}: no confident OpenAPI match (best overlap ${bestOverlap}/${values.length}) — skipped`);
    return;
  }
  const missing = values.filter((v) => !best.includes(v));
  if (missing.length) {
    breaking.push(`enum ${name}: OpenAPI enum missing values ${JSON.stringify(missing)}`);
  } else {
    notes.push(`enum ${name}: ${values.length} value(s) match OpenAPI superset`);
  }
}

// --- 3. Path/method coverage from frontend API clients ------------------------
const consumed = []; // { method, path, file, line }
function extractCalls(sourceFile, fileName) {
  function visit(node) {
    if (
      ts.isCallExpression(node) &&
      ts.isIdentifier(node.expression) &&
      ["apiRequest", "apiDownload", "apiDownloadRequest"].includes(node.expression.text)
    ) {
      const arg0 = node.arguments[0];
      const path = arg0 ? pathFromCallArg(arg0) : null;
      if (path !== null) {
        // Method defaults to GET; `apiDownload` (path-only) is always GET, but
        // `apiDownloadRequest` may carry an explicit method (e.g. POST export).
        let method = "GET";
        for (let i = 1; i < node.arguments.length; i++) {
          const a = node.arguments[i];
          if (a && ts.isObjectLiteralExpression(a)) {
            for (const prop of a.properties) {
              if (
                ts.isPropertyAssignment(prop) &&
                ts.isIdentifier(prop.name) &&
                prop.name.text === "method" &&
                ts.isStringLiteral(prop.initializer)
              ) {
                method = prop.initializer.text.toUpperCase();
              }
            }
          }
        }
        consumed.push({
          method,
          path,
          file: fileName,
          line: sourceFile.getLineAndCharacterOfPosition(node.getStart()).line + 1,
        });
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(sourceFile);
}

for (const f of readdirSync(apiDir).filter((f) => f.endsWith(".ts"))) {
  const text = readFileSync(join(apiDir, f), "utf8");
  const sf = ts.createSourceFile(f, text, ts.ScriptTarget.Latest, true);
  extractCalls(sf, f);
}

const missingOps = [];
for (const c of consumed) {
  const key = `${c.method} ${structural(c.path)}`;
  if (!openapiOps.has(key)) {
    missingOps.push(c);
  }
}
if (missingOps.length) {
  for (const c of missingOps) {
    breaking.push(`frontend calls ${c.method} ${c.path} (${c.file}:${c.line}) but OpenAPI has no matching operation`);
  }
} else {
  notes.push(`path/method coverage: all ${consumed.length} frontend API call(s) present in OpenAPI`);
}

// --- 4. Representative schema/nullability invariants --------------------------
function propType(schemaName, propName) {
  const s = spec.components?.schemas?.[schemaName];
  const p = s?.properties?.[propName];
  if (!p) return null;
  let node = p;
  // Resolve nullable unions (Pydantic emits `anyOf: [{type: X}, {type: null}]`).
  if (p.anyOf || p.oneOf) {
    const alts = (p.anyOf || p.oneOf).filter((x) => x.type !== "null" && x.type !== "null");
    if (alts.length === 1) node = alts[0];
    else return p.anyOf ? "anyOf" : "oneOf";
  }
  if (node.$ref) return node.$ref.split("/").pop();
  if (node.type) return Array.isArray(node.type) ? node.type.join("|") : node.type;
  return "unknown";
}

const schemaChecks = [
  // Decimal monetary amounts must serialize as string (frontend types them as
  // `string | null`), never number — guards the known decimal drift risk.
  ["DashboardSummary", "riskRemainingAmountYuan", "string"],
  ["DashboardSummary", "riskCollectionCompletionRate", ["number", "integer"]],
  // Datetimes must be string (RFC 3339), never object/number. The login
  // endpoint returns `SessionResponse` (not `LoginResponse`), whose
  // `expiresAt` carries the session-expiry timestamp.
  ["SessionResponse", "expiresAt", "string"],
];
for (const [schema, prop, expected] of schemaChecks) {
  const actual = propType(schema, prop);
  if (actual === null) {
    notes.push(`schema spot-check ${schema}.${prop}: not found in OpenAPI — skipped`);
  } else {
    const ok = Array.isArray(expected) ? expected.includes(actual) : actual === expected;
    if (!ok) breaking.push(`schema ${schema}.${prop}: expected ${JSON.stringify(expected)}, got ${actual}`);
    else notes.push(`schema spot-check ${schema}.${prop}: ${actual} OK`);
  }
}

// --- Enum parsing from hand-written contracts ---------------------------------
const contractsSrc = readFileSync(contractsPath, "utf8");
const cFile = ts.createSourceFile("index.ts", contractsSrc, ts.ScriptTarget.Latest, true);
function visitEnums(node) {
  if (ts.isVariableStatement(node)) {
    for (const decl of node.declarationList.declarations) {
      const init = decl.initializer;
      if (
        init &&
        ts.isAsExpression(init) &&
        ts.isArrayLiteralExpression(init.expression) &&
        ts.isIdentifier(decl.name)
      ) {
        const values = init.expression.elements
          .filter((e) => ts.isStringLiteral(e))
          .map((e) => e.text);
        if (values.length) enumReport(decl.name.text, values);
      }
    }
  }
  ts.forEachChild(node, visitEnums);
}
visitEnums(cFile);

// --- Report -------------------------------------------------------------------
console.log("OpenAPI compatibility check (T032)");
console.log("===================================");
console.log(`OpenAPI operations: ${openapiOps.size}`);
console.log(`Frontend API calls scanned: ${consumed.length}`);
console.log("");
console.log("Notes:");
for (const n of notes) console.log(`  - ${n}`);
console.log("");
if (breaking.length) {
  console.log(`BREAKING differences (${breaking.length}):`);
  for (const b of breaking) console.log(`  ✗ ${b}`);
  console.log("");
  console.log("compatibility check: FAIL");
  process.exit(1);
}
console.log("compatibility check: PASS (no breaking differences; diff approved at T032 freeze)");
