/** Return the ISO week label for the user's current local date. */
export function currentWeekLabel(date = new Date()): string {
  const local = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const day = local.getDay() || 7;
  local.setDate(local.getDate() + 4 - day);
  const yearStart = new Date(local.getFullYear(), 0, 1);
  const week = Math.ceil((((local.getTime() - yearStart.getTime()) / 86_400_000) + 1) / 7);
  return `${local.getFullYear()}年第${week}周`;
}
