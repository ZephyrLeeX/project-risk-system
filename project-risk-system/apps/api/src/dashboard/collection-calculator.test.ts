import { Prisma } from "@prisma/client";
import { describe, expect, it } from "vitest";

import {
  collectionCompletionRate,
  resolveNextCollection,
  resolveProjectCollectionAmounts,
} from "./collection-calculator";

const decimal = (value: string) => new Prisma.Decimal(value);

describe("department collection amount resolution", () => {
  it("uses a complete project-list amount set before supplemental data", () => {
    const result = resolveProjectCollectionAmounts({
      annualPlanAmount: decimal("1000"),
      actualCollectedAmount: decimal("300"),
      remainingAmount: decimal("700"),
      supplementalRows: [
        {
          contractReceivableAmount: decimal("500"),
          cumulativeCollectedAmount: decimal("200"),
          remainingUncollectedAmount: null,
        },
      ],
    });

    expect(result.source).toBe("PROJECT_LIST");
    expect(result.receivable?.toFixed(2)).toBe("1000.00");
  });

  it("derives supplemental remaining amounts without manufacturing source rows", () => {
    const result = resolveProjectCollectionAmounts({
      annualPlanAmount: null,
      actualCollectedAmount: null,
      remainingAmount: null,
      supplementalRows: [
        {
          contractReceivableAmount: decimal("315000"),
          cumulativeCollectedAmount: decimal("15000"),
          remainingUncollectedAmount: null,
        },
      ],
    });

    expect(result.source).toBe("SUPPLEMENTAL");
    expect(result.receivable?.toFixed(2)).toBe("315000.00");
    expect(result.collected?.toFixed(2)).toBe("15000.00");
    expect(result.remaining?.toFixed(2)).toBe("300000.00");
    expect(collectionCompletionRate(result.receivable, result.collected)).toBe(
      4.8,
    );
  });

  it("keeps incomplete sources explicit", () => {
    const result = resolveProjectCollectionAmounts({
      annualPlanAmount: null,
      actualCollectedAmount: null,
      remainingAmount: null,
      supplementalRows: [],
    });

    expect(result.source).toBe("MISSING");
    expect(result.complete).toBe(false);
    expect(collectionCompletionRate(null, null)).toBeNull();
  });

  it("selects the first planned monthly amount from the current month", () => {
    const result = resolveNextCollection(
      [
        { month: 5, amount: decimal("100"), attribute: "实际" },
        { month: 6, amount: decimal("200"), attribute: "预计" },
        { month: 8, amount: decimal("300"), attribute: "预计" },
      ],
      "8月回款",
      7,
    );

    expect(result.source).toBe("MONTHLY_PLAN");
    expect(result.month).toBe(8);
    expect(result.amount?.toFixed(2)).toBe("300.00");
  });

  it("uses the imported collection progress when no monthly amount exists", () => {
    const result = resolveNextCollection(
      [{ month: 8, amount: null, attribute: "预计" }],
      "待客户上会后排付款计划",
      7,
    );

    expect(result.source).toBe("PROGRESS_TEXT");
    expect(result.label).toBe("待客户上会后排付款计划");
  });
});
