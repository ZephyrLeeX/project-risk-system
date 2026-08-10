import { Prisma } from "@prisma/client";

export interface SupplementalCollectionAmounts {
  contractReceivableAmount: Prisma.Decimal | null;
  cumulativeCollectedAmount: Prisma.Decimal | null;
  remainingUncollectedAmount: Prisma.Decimal | null;
}

export interface ProjectCollectionAmountsInput {
  annualPlanAmount: Prisma.Decimal | null;
  actualCollectedAmount: Prisma.Decimal | null;
  remainingAmount: Prisma.Decimal | null;
  supplementalRows: SupplementalCollectionAmounts[];
}

export interface ResolvedCollectionAmounts {
  source: "PROJECT_LIST" | "SUPPLEMENTAL" | "MISSING";
  supplementalRowCount: number;
  receivable: Prisma.Decimal | null;
  collected: Prisma.Decimal | null;
  remaining: Prisma.Decimal | null;
  complete: boolean;
}

export interface MonthlyCollectionAmount {
  month: number;
  amount: Prisma.Decimal | null;
  attribute: string | null;
}

export interface ResolvedNextCollection {
  source: "MONTHLY_PLAN" | "PROGRESS_TEXT" | "MISSING";
  month: number | null;
  amount: Prisma.Decimal | null;
  attribute: string | null;
  label: string;
}

const zero = () => new Prisma.Decimal(0);

function nonNegative(value: Prisma.Decimal): Prisma.Decimal {
  return value.lessThan(0) ? zero() : value;
}

function completeTriple(
  receivable: Prisma.Decimal | null,
  collected: Prisma.Decimal | null,
  remaining: Prisma.Decimal | null,
): {
  receivable: Prisma.Decimal | null;
  collected: Prisma.Decimal | null;
  remaining: Prisma.Decimal | null;
  complete: boolean;
} {
  let resolvedReceivable = receivable;
  let resolvedCollected = collected;
  let resolvedRemaining = remaining;
  if (!resolvedReceivable && resolvedCollected && resolvedRemaining) {
    resolvedReceivable = resolvedCollected.add(resolvedRemaining);
  }
  if (!resolvedCollected && resolvedReceivable && resolvedRemaining) {
    resolvedCollected = nonNegative(
      resolvedReceivable.sub(resolvedRemaining),
    );
  }
  if (!resolvedRemaining && resolvedReceivable && resolvedCollected) {
    resolvedRemaining = nonNegative(
      resolvedReceivable.sub(resolvedCollected),
    );
  }
  return {
    receivable: resolvedReceivable,
    collected: resolvedCollected,
    remaining: resolvedRemaining,
    complete: Boolean(
      resolvedReceivable && resolvedCollected && resolvedRemaining,
    ),
  };
}

export function resolveProjectCollectionAmounts(
  input: ProjectCollectionAmountsInput,
): ResolvedCollectionAmounts {
  const projectAmounts = completeTriple(
    input.annualPlanAmount,
    input.actualCollectedAmount,
    input.remainingAmount,
  );
  if (projectAmounts.complete) {
    return {
      source: "PROJECT_LIST",
      supplementalRowCount: input.supplementalRows.length,
      ...projectAmounts,
    };
  }

  if (input.supplementalRows.length > 0) {
    const supplementalAmounts = input.supplementalRows.map((row) =>
      completeTriple(
        row.contractReceivableAmount,
        row.cumulativeCollectedAmount,
        row.remainingUncollectedAmount,
      ),
    );
    if (supplementalAmounts.every((row) => row.complete)) {
      return {
        source: "SUPPLEMENTAL",
        supplementalRowCount: supplementalAmounts.length,
        receivable: supplementalAmounts.reduce(
          (sum, row) => sum.add(row.receivable!),
          zero(),
        ),
        collected: supplementalAmounts.reduce(
          (sum, row) => sum.add(row.collected!),
          zero(),
        ),
        remaining: supplementalAmounts.reduce(
          (sum, row) => sum.add(row.remaining!),
          zero(),
        ),
        complete: true,
      };
    }
  }

  return {
    source:
      input.annualPlanAmount ||
      input.actualCollectedAmount ||
      input.remainingAmount
        ? "PROJECT_LIST"
        : input.supplementalRows.length
          ? "SUPPLEMENTAL"
          : "MISSING",
    supplementalRowCount: input.supplementalRows.length,
    ...projectAmounts,
  };
}

export function collectionCompletionRate(
  receivable: Prisma.Decimal | null,
  collected: Prisma.Decimal | null,
): number | null {
  if (!receivable || !collected || receivable.lessThanOrEqualTo(0)) {
    return null;
  }
  return Number(collected.div(receivable).mul(100).toFixed(1));
}

function isPlannedAttribute(value: string | null): boolean {
  return value === null || /预计|计划/.test(value);
}

export function resolveNextCollection(
  monthlyCollections: MonthlyCollectionAmount[],
  collectionProgress: string | null,
  asOfMonth: number,
): ResolvedNextCollection {
  const planned = monthlyCollections
    .filter(
      (item) =>
        item.month >= 1 &&
        item.month <= 12 &&
        item.amount !== null &&
        item.amount.greaterThan(0) &&
        isPlannedAttribute(item.attribute),
    )
    .sort((left, right) => left.month - right.month);
  const next =
    planned.find((item) => item.month >= asOfMonth) ?? planned[0];
  if (next) {
    return {
      source: "MONTHLY_PLAN",
      month: next.month,
      amount: next.amount,
      attribute: next.attribute,
      label: `${next.month}月 · ${next.attribute ?? "计划回款"}`,
    };
  }
  const progress = collectionProgress?.trim();
  if (progress) {
    return {
      source: "PROGRESS_TEXT",
      month: null,
      amount: null,
      attribute: null,
      label: progress,
    };
  }
  return {
    source: "MISSING",
    month: null,
    amount: null,
    attribute: null,
    label: "待补充回款节点",
  };
}
