// T032 reproducible frontend type generation.
//
// Reads the frozen FastAPI OpenAPI authority (openapi/openapi.json), applies a
// documented codegen normalization, and writes src/generated/openapi.ts. The
// generated file is the sole post-cutover contract authority and must never be
// hand-edited; rebuild it with `pnpm --filter @risk-platform/contracts gen`.
//
// Normalization: the backend models arbitrary-JSON fields (weekly-report
// summary/riskLevelCounts, agent tool result data) with a recursive `JSONValue`
// type. Pydantic emits it as a self-referential OpenAPI component which
// openapi-typescript renders as a recursive indexed-access type that violates
// strict TypeScript (TS2502). The frontend already consumes these fields as
// `unknown` / `Record<string, unknown>`, so the two recursive components are
// normalized to an unconstrained schema (-> `unknown`) for codegen only. The
// frozen openapi.json keeps the precise recursive schema and remains the
// authority; only the generated TypeScript is normalized.
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import openapiTS, { astToString, COMMENT_HEADER } from "openapi-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const pkgRoot = join(here, "..");
const specPath = join(pkgRoot, "openapi", "openapi.json");
const outPath = join(pkgRoot, "src", "generated", "openapi.ts");

const spec = JSON.parse(readFileSync(specPath, "utf8"));

const schemas = spec.components?.schemas;
if (schemas) {
  for (const name of ["JSONValue", "JSONScalar"]) {
    if (name in schemas) {
      schemas[name] = {
        description:
          "Arbitrary JSON value; normalized to `unknown` for TypeScript codegen (T032).",
      };
    }
  }
}

const ast = await openapiTS(spec, { alphabetize: true });
mkdirSync(dirname(outPath), { recursive: true });
writeFileSync(outPath, (COMMENT_HEADER + astToString(ast)).replace(/\n+$/, "\n"), "utf8");
console.log(`Generated: ${outPath}`);
