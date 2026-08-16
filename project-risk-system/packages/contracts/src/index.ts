export const ROLE_CODES = [
  "SYSTEM_ADMIN",
  "RISK_ADMIN",
  "PROJECT_MANAGER",
  "VIEWER_AUDITOR",
] as const;

export type RoleCode = (typeof ROLE_CODES)[number];

export const DATA_SCOPE_TYPES = [
  "ALL",
  "OWNED",
  "ASSIGNED",
  "OWNED_OR_ASSIGNED",
  "NONE",
] as const;

export type DataScopeType = (typeof DATA_SCOPE_TYPES)[number];

export interface ApiResponse<T> {
  code: string;
  message: string;
  data: T;
  traceId: string;
}

export interface HealthResponse {
  service: "project-risk-api";
  status: "ok";
  version: string;
  timestamp: string;
}

export interface UserSummary {
  id: string;
  displayName: string;
  username: string;
  departmentName: string | null;
  roleCodes: RoleCode[];
  dataScope: DataScopeType;
  enabled: boolean;
}

export interface LoginRequest {
  username: string;
  password: string;
}

export interface ChangePasswordRequest {
  currentPassword: string;
  newPassword: string;
  confirmPassword: string;
}

export interface AuthenticatedUser {
  id: string;
  username: string;
  displayName: string;
  departmentName: string | null;
  roleCodes: RoleCode[];
  permissions: string[];
  dataScope: DataScopeType;
  mustChangePassword: boolean;
}

export interface LoginResponse {
  user: AuthenticatedUser;
  expiresAt: string;
}

export interface SessionResponse {
  user: AuthenticatedUser;
  expiresAt: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  page: number;
  pageSize: number;
  total: number;
}

export interface DepartmentOption {
  id: string;
  code: string;
  name: string;
}

export interface ProjectOption {
  id: string;
  externalCode: string | null;
  name: string;
  departmentName: string | null;
}

export type SystemConfigModule = "ALL" | "RISK" | "MAIL" | "ALIAS" | "SECURITY" | "NOTIFICATION";
export type ConfigRiskLevel = "HIGH" | "MEDIUM" | "LOW";

export interface SystemRiskCategory {
  id: string | null;
  code: string;
  name: string;
  keywords: string[];
  colorToken: string;
  description: string | null;
  defaultLevel: ConfigRiskLevel | null;
  sortOrder: number;
  isActive: boolean;
  riskCount: number;
}

export interface SystemRiskLevelRule {
  level: ConfigRiskLevel;
  displayName: string;
  colorToken: string;
  criteria: string;
  keywords: string[];
  sortOrder: number;
  isActive: boolean;
}

export interface SystemProjectAlias {
  id: string | null;
  projectId: string;
  projectName: string;
  projectCode: string | null;
  projectOwnerName: string | null;
  alias: string;
  source: string;
  note: string | null;
  isActive: boolean;
  hitCount: number;
  lastHitAt: string | null;
}

export interface SystemMailSettings {
  syncIntervalMinutes: number;
  initialSyncDays: number;
  subjectKeywords: string[];
  riskKeywords: string[];
}

export interface SystemSecuritySettings {
  sessionHours: number;
  idleTimeoutMinutes: number;
  loginMaxAttempts: number;
  loginLockMinutes: number;
  passwordMinLength: number;
}

export interface SystemNotificationSettings {
  mailboxSyncFailure: boolean;
  apiKeyExpiry: boolean;
  apiKeyExpiryDays: number;
  importFailure: boolean;
  abnormalLogin: boolean;
}

export interface SystemConfigSnapshot {
  categories: SystemRiskCategory[];
  levels: SystemRiskLevelRule[];
  aliases: SystemProjectAlias[];
  mail: SystemMailSettings;
  security: SystemSecuritySettings;
  notifications: SystemNotificationSettings;
}

export interface SystemConfigOverview {
  version: string;
  publishedAt: string;
  publishedBy: string;
  changeSummary: string;
  activeConfigCount: number;
  activeCategoryCount: number;
  activeLevelCount: number;
  monthlyChangeCount: number;
  lastMailboxSyncAt: string | null;
  nextMailboxSyncAt: string | null;
  authorizedMailboxCount: number;
  snapshot: SystemConfigSnapshot;
}

export interface PublishSystemConfigRequest extends SystemConfigSnapshot {
  changeCount: number;
  changeSummary: string;
  module: SystemConfigModule;
}

export interface SystemConfigReleaseItem {
  id: string;
  version: string;
  module: SystemConfigModule;
  changeCount: number;
  changeSummary: string;
  impactScope: string[];
  publishedAt: string;
  publishedBy: string;
  traceId: string;
}

export interface SystemConfigReleaseDetail extends SystemConfigReleaseItem {
  beforeSnapshot: SystemConfigSnapshot | null;
  snapshot: SystemConfigSnapshot;
}

export interface PermissionItem {
  id: string;
  code: string;
  name: string;
  module: string;
  description: string | null;
}

export interface RoleListItem {
  id: string;
  code: string;
  name: string;
  description: string | null;
  isSystem: boolean;
  enabled: boolean;
  defaultDataScope: DataScopeType;
  userCount: number;
  permissionCodes: string[];
  updatedAt: string;
}

export interface AdminUserListItem {
  id: string;
  username: string;
  displayName: string;
  email: string | null;
  department: DepartmentOption | null;
  status: "ACTIVE" | "DISABLED" | "LOCKED";
  role: RoleListItem | null;
  dataScope: DataScopeType;
  assignedProjectIds: string[];
  assignedProjectCount: number;
  mustChangePassword: boolean;
  lastLoginAt: string | null;
  lockedUntil: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface AdminUserSummary {
  total: number;
  active: number;
  locked: number;
  disabled: number;
}

export interface UserMutationRequest {
  displayName: string;
  username: string;
  email?: string | null;
  departmentId: string;
  roleId: string;
  dataScope: DataScopeType;
  projectIds: string[];
  enabled: boolean;
}

export interface UserMutationResponse {
  user: AdminUserListItem;
  initialPassword?: string;
}

export interface RoleMutationRequest {
  name: string;
  code: string;
  description?: string | null;
  enabled: boolean;
  defaultDataScope: DataScopeType;
  permissionCodes: string[];
}

export interface UserAuditRecord {
  id: string;
  action: string;
  result: "SUCCESS" | "FAILURE";
  actorName: string | null;
  createdAt: string;
  summary: string;
}

export type AiConnectionStatus = "UNTESTED" | "HEALTHY" | "FAILED";
export type AiProviderStatusFilter = "ACTIVE" | "DISABLED";
export type AiCallResult = "SUCCESS" | "FAILURE";
export type AiCallScene =
  | "WEEKLY_REPORT"
  | "AGENT_QUERY"
  | "RISK_EXTRACTION"
  | "CONNECTION_TEST";

export interface AiProviderSummary {
  total: number;
  healthy: number;
  expiring: number;
  sevenDayCallTotal: number;
  sevenDaySuccessRate: number;
}

export interface AiProviderListItem {
  id: string;
  name: string;
  vendor: string;
  endpoint: string;
  protocol: AiProviderProtocol;
  model: string;
  maskedKey: string;
  expiresAt: string | null;
  timeoutSeconds: number;
  retryCount: number;
  enabled: boolean;
  isDefault: boolean;
  priority: number;
  lastTestStatus: AiConnectionStatus;
  lastTestAt: string | null;
  lastTestLatencyMs: number | null;
  lastTestErrorCode: string | null;
  sevenDayUsageCount: number;
  createdAt: string;
  updatedAt: string;
}

export interface AiProviderMutationRequest {
  name: string;
  vendor: string;
  endpoint: string;
  protocol: AiProviderProtocol;
  model: string;
  expiresAt?: string | null;
  timeoutSeconds: number;
  retryCount: number;
  enabled: boolean;
}

export interface CreateAiProviderRequest extends AiProviderMutationRequest {
  apiKey: string;
}

export interface RotateAiProviderKeyRequest {
  apiKey: string;
  expiresAt?: string | null;
}

export interface SetAiProviderStatusRequest {
  enabled: boolean;
}

export interface AiConnectionTestRequest {
  name: string;
  endpoint: string;
  protocol: AiProviderProtocol;
  model: string;
  apiKey: string;
  timeoutSeconds: number;
  retryCount: number;
}

export type AiProviderProtocol = "OPENAI_CHAT_COMPLETIONS" | "OPENAI_RESPONSES" | "ANTHROPIC_MESSAGES";

export interface AiConnectionTestResult {
  providerId: string | null;
  providerName: string;
  model: string;
  success: boolean;
  latencyMs: number;
  errorCode: string | null;
  errorSummary: string | null;
  testedAt: string;
  traceId: string;
}

export interface AiProviderStrategyItem {
  id: string;
  name: string;
  enabled: boolean;
  isDefault: boolean;
  priority: number;
}

export interface AiUsageTrendItem {
  date: string;
  count: number;
}

export interface AiUsageOverview {
  rangeStart: string;
  rangeEnd: string;
  callTotal: number;
  successTotal: number;
  successRate: number;
  averageDurationMs: number;
  p95DurationMs: number;
  totalTokens: number;
  trend: AiUsageTrendItem[];
}

export interface AiCallLogListItem {
  id: string;
  traceId: string;
  providerName: string;
  model: string;
  scene: AiCallScene;
  totalTokens: number;
  durationMs: number;
  result: AiCallResult;
  errorCode: string | null;
  errorSummary: string | null;
  createdAt: string;
}

export interface AiCallLogDetail extends AiCallLogListItem {
  inputTokens: number;
  outputTokens: number;
  actorDisplayName: string | null;
  dataProtectionNotice: string;
}

export type ImportBatchStatus =
  | "PREVIEWED"
  | "IMPORTED"
  | "ROLLED_BACK"
  | "FAILED";

export type ImportRowStatus =
  | "READY"
  | "WARNING"
  | "ERROR"
  | "IMPORTED"
  | "ROLLED_BACK";

export type ImportRowAction = "CREATE" | "UPDATE" | "SKIP";

export type ProjectRiskLevel = "HIGH" | "MEDIUM" | "LOW" | "UNKNOWN";

export type RiskStatus = "ACTIVE" | "RESOLVED";

export type RiskSourceType =
  | "EXCEL"
  | "LITIGATION"
  | "MAIL_AI"
  | "MANUAL";

export interface DashboardSummary {
  projectTotal: number;
  deliveryProjectTotal: number;
  deliveryDepartmentTotal: number;
  latestImportBatchCode: string | null;
  latestImportCreatedProjectTotal: number;
  activeRiskTotal: number;
  highRiskTotal: number;
  mediumRiskTotal: number;
  lowRiskTotal: number;
  unknownRiskTotal: number;
  riskProjectTotal: number;
  highRiskProjectTotal: number;
  weeklyNewRiskTotal: number;
  weeklyNewHighRiskTotal: number;
  mailAiRiskTotal: number;
  manualRiskTotal: number;
  excelRiskTotal: number;
  litigationRiskTotal: number;
  highRiskFocusProjectNames: string[];
  highRiskPriorityItems: string[];
  riskRemainingAmountYuan: string | null;
  riskCollectedAmountYuan: string | null;
  riskAmountCompleteProjectTotal: number;
  riskAmountMissingProjectTotal: number;
  riskCollectionCompletionRate: number | null;
  updatedAt: string | null;
  dataScope: DataScopeType;
}

export type CollectionAmountSource =
  | "PROJECT_LIST"
  | "SUPPLEMENTAL"
  | "MISSING";

export interface DepartmentCollectionTotals {
  projectTotal: number;
  amountCompleteProjectTotal: number;
  amountMissingProjectTotal: number;
  receivableAmountYuan: string | null;
  collectedAmountYuan: string | null;
  remainingAmountYuan: string | null;
  completionRate: number | null;
}

export interface DepartmentCollectionSummaryItem
  extends DepartmentCollectionTotals {
  departmentId: string | null;
  departmentKey: string;
  departmentName: string;
}

export interface DepartmentCollectionSummary {
  items: DepartmentCollectionSummaryItem[];
  totals: DepartmentCollectionTotals;
  pendingSupplementalCount: number | null;
  pendingSupplementalReceivableAmountYuan: string | null;
  updatedAt: string | null;
  dataScope: DataScopeType;
}

export interface DepartmentCollectionProjectItem {
  projectId: string;
  externalCode: string | null;
  projectName: string;
  ownerName: string | null;
  amountSource: CollectionAmountSource;
  amountSourceLabel: string;
  supplementalRowCount: number;
  receivableAmountYuan: string | null;
  collectedAmountYuan: string | null;
  remainingAmountYuan: string | null;
  completionRate: number | null;
}

export interface DepartmentCollectionDetail {
  departmentId: string | null;
  departmentKey: string;
  departmentName: string;
  summary: DepartmentCollectionTotals;
  projects: DepartmentCollectionProjectItem[];
  updatedAt: string | null;
}

export type NextCollectionSource =
  | "MONTHLY_PLAN"
  | "PROGRESS_TEXT"
  | "MISSING";

export interface NextCollectionInfo {
  source: NextCollectionSource;
  month: number | null;
  attribute: string | null;
  amountYuan: string | null;
  label: string;
}

export interface RiskCollectionProjectItem
  extends DepartmentCollectionProjectItem {
  departmentName: string | null;
  riskLevel: ProjectRiskLevel;
  activeRiskTotal: number;
  collectionProgress: string | null;
  nextCollection: NextCollectionInfo;
  updatedAt: string | null;
}

export interface RiskCollectionListResponse {
  items: RiskCollectionProjectItem[];
  totals: DepartmentCollectionTotals;
  riskProjectTotal: number;
  owners: string[];
  updatedAt: string | null;
  dataScope: DataScopeType;
}

export interface RiskCollectionMonthItem {
  month: number;
  attribute: string | null;
  amountYuan: string | null;
}

export interface RiskCollectionDetail extends RiskCollectionProjectItem {
  monthlyCollections: RiskCollectionMonthItem[];
  activeRisks: Array<{
    id: string;
    title: string;
    description: string;
    level: ProjectRiskLevel;
    categoryName: string;
    sourceLabel: string;
    detectedAt: string;
  }>;
  statisticalScope: string;
}

export type RiskTimelineEventType =
  | "RISK_CREATED"
  | "RISK_UPDATED"
  | "LEVEL_CHANGED"
  | "ACTION_CREATED"
  | "ACTION_UPDATED"
  | "ACTION_STATUS_CHANGED"
  | "ACTION_COMPLETED"
  | "RISK_RESOLVED"
  | "RISK_REOPENED";

export type RiskTimelineTone =
  | "RED"
  | "ORANGE"
  | "BLUE"
  | "GREEN"
  | "GRAY";

export interface RiskTimelineItem {
  id: string;
  eventType: RiskTimelineEventType;
  eventLabel: string;
  tone: RiskTimelineTone;
  projectId: string;
  projectName: string;
  departmentName: string | null;
  projectOwnerName: string | null;
  riskId: string;
  riskTitle: string;
  riskLevel: ProjectRiskLevel;
  riskStatus: RiskStatus;
  categoryName: string;
  title: string;
  description: string;
  fromValue: string | null;
  toValue: string | null;
  actorName: string;
  sourceLabel: string;
  occurredAt: string;
}

export interface RiskTimelineSummary {
  total: number;
  riskCreated: number;
  riskChanged: number;
  actionProgress: number;
  resolved: number;
}

export interface RiskTimelineListResponse
  extends PaginatedResponse<RiskTimelineItem> {
  summary: RiskTimelineSummary;
  projects: Array<{
    id: string;
    name: string;
  }>;
  updatedAt: string | null;
  dataScope: DataScopeType;
}

export interface RiskTimelineDetail extends RiskTimelineItem {
  riskDescription: string;
  riskEvidence: string | null;
  riskSuggestion: string | null;
  detectedAt: string;
  resolvedAt: string | null;
  resolutionReason: string | null;
  actionItem: {
    id: string;
    title: string;
    status: ActionItemStatus;
    assigneeName: string;
    dueDate: string | null;
    completionNote: string | null;
  } | null;
  metadata: Record<string, unknown> | null;
}

export interface RiskCategoryOption {
  id: string;
  code: string;
  name: string;
}

export interface DashboardRiskListItem {
  id: string;
  projectId: string;
  projectExternalCode: string | null;
  projectName: string;
  departmentName: string | null;
  projectOwnerName: string | null;
  title: string;
  description: string;
  evidence: string | null;
  suggestion: string | null;
  level: ProjectRiskLevel;
  status: RiskStatus;
  category: RiskCategoryOption;
  sourceType: RiskSourceType;
  sourceLabel: string;
  reporterName: string | null;
  weekCode: string | null;
  actualCollectedAmountYuan: string | null;
  remainingAmountYuan: string | null;
  detectedAt: string;
  updatedAt: string;
}

export type DashboardFocusItem = DashboardRiskListItem;

export interface DashboardRiskListResponse
  extends PaginatedResponse<DashboardRiskListItem> {}

export interface DashboardRiskFilterOptions {
  categories: RiskCategoryOption[];
  owners: string[];
}

export interface DashboardRiskDetail extends DashboardRiskListItem {
  resolvedAt: string | null;
  resolvedByName: string | null;
  resolutionReason: string | null;
  sameProjectRisks: Array<{
    id: string;
    title: string;
    level: ProjectRiskLevel;
    status: RiskStatus;
    categoryName: string;
  }>;
}

export interface ResolvedRiskListItem extends DashboardRiskListItem {
  resolvedAt: string;
  resolvedByName: string;
  resolutionReason: string;
}

export interface ResolvedRiskListResponse
  extends PaginatedResponse<ResolvedRiskListItem> {
  owners: string[];
  updatedAt: string | null;
  dataScope: DataScopeType;
}

export interface ResolveRiskRequest {
  reason: string;
}

export interface ReopenRiskRequest {
  reason: string;
}

export type ActionItemUrgency = "EMERGENCY" | "HIGH" | "NORMAL";

export type ActionItemStatus =
  | "PENDING"
  | "IN_PROGRESS"
  | "COMPLETED";

export type ActionItemSourceType = "RISK_SUGGESTION" | "MANUAL";

export interface ManagerTodoItem {
  id: string;
  riskId: string | null;
  projectId: string;
  projectName: string;
  projectOwnerName: string | null;
  departmentName: string | null;
  title: string;
  description: string;
  urgency: ActionItemUrgency;
  status: ActionItemStatus;
  sourceType: ActionItemSourceType;
  typeLabel: string;
  assigneeUserId: string | null;
  assigneeName: string;
  dueDate: string | null;
  completionNote: string | null;
  completedAt: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface ManagerTodoSummary {
  total: number;
  pending: number;
  inProgress: number;
  completed: number;
  emergency: number;
}

export interface ManagerTodoScheduleItem {
  weekday: string;
  date: string;
  actionItemId: string;
  title: string;
  projectName: string;
  assigneeName: string;
  urgency: ActionItemUrgency;
}

export interface ManagerTodoListResponse {
  items: ManagerTodoItem[];
  page: number;
  pageSize: number;
  total: number;
  summary: ManagerTodoSummary;
  owners: string[];
  schedule: ManagerTodoScheduleItem[];
  updatedAt: string | null;
  dataScope: DataScopeType;
}

export interface ManagerTodoDetail extends ManagerTodoItem {
  risk: {
    id: string;
    title: string;
    description: string;
    evidence: string | null;
    suggestion: string | null;
    level: ProjectRiskLevel;
    status: RiskStatus;
    categoryName: string;
    sourceLabel: string;
    detectedAt: string;
  } | null;
}

export interface UpdateManagerTodoRequest {
  status?: ActionItemStatus;
  assigneeName?: string;
  dueDate?: string | null;
  completionNote?: string | null;
}

export type SupplementalMatchStatus =
  | "MATCHED"
  | "UNMATCHED"
  | "AMBIGUOUS";

export type LegalMatterMatchStatus =
  | "MATCHED"
  | "UNMATCHED"
  | "AMBIGUOUS";

export interface ProjectImportRowItem {
  id: string;
  rowNumber: number;
  action: ImportRowAction;
  status: ImportRowStatus;
  externalCode: string | null;
  projectName: string | null;
  departmentName: string | null;
  deliveryOwnerName: string | null;
  annualPlanAmount: string | null;
  actualCollectedAmount: string | null;
  remainingAmount: string | null;
  collectionRiskLevel: ProjectRiskLevel;
  collectionProgress: string | null;
  warnings: string[];
  errors: string[];
  matchedProjectId: string | null;
  committedProjectId: string | null;
}

export interface ProjectImportBatchSummary {
  id: string;
  fileName: string;
  fileHash: string;
  status: ImportBatchStatus;
  sheetName: string;
  totalRows: number;
  readyRows: number;
  warningRows: number;
  errorRows: number;
  createdRows: number;
  updatedRows: number;
  supplementalTotalRows: number;
  supplementalMatchedRows: number;
  supplementalUnmatchedRows: number;
  supplementalAmbiguousRows: number;
  supplementalWarningRows: number;
  supplementalErrorRows: number;
  legalTotalRows: number;
  legalMatchedRows: number;
  legalUnmatchedRows: number;
  legalAmbiguousRows: number;
  legalWarningRows: number;
  legalErrorRows: number;
  uploadedByName: string;
  createdAt: string;
  confirmedAt: string | null;
  rolledBackAt: string | null;
}

export interface SupplementalCollectionRowItem {
  id: string;
  rowNumber: number;
  status: ImportRowStatus;
  matchStatus: SupplementalMatchStatus;
  projectId: string | null;
  matchedProject: ProjectOption | null;
  externalCode: string | null;
  projectName: string | null;
  contractReceivableAmount: string | null;
  procurementContractAmount: string | null;
  cumulativeCollectedAmount: string | null;
  remainingUncollectedAmount: string | null;
  actualCollectedThisYear: string | null;
  actualCollectedNetThisYear: string | null;
  annualCollectionPlan: string | null;
  collectionRiskLevel: ProjectRiskLevel;
  afterYearAmount: string | null;
  warnings: string[];
  errors: string[];
}

export interface LegalMatterRowItem {
  id: string;
  rowNumber: number;
  status: ImportRowStatus;
  matchStatus: LegalMatterMatchStatus;
  projectId: string | null;
  externalCode: string | null;
  projectName: string | null;
  departmentName: string | null;
  deliveryOwnerName: string | null;
  annualPlanAmount: string | null;
  collectionRiskLevel: ProjectRiskLevel;
  legalProgress: string | null;
  warnings: string[];
  errors: string[];
}

export interface ProjectImportBatchDetail
  extends ProjectImportBatchSummary {
  sourceMeta: {
    sheetNames: string[];
    monthAttributes: Record<string, string | null>;
    ignoredSheets: string[];
  };
  rows: ProjectImportRowItem[];
  supplementalRows: SupplementalCollectionRowItem[];
  legalRows: LegalMatterRowItem[];
}

export interface ConfirmProjectImportRequest {
  acknowledgeWarnings: boolean;
}

export interface MatchSupplementalCollectionRequest {
  projectId: string;
}

export type AuditLogResult = "SUCCESS" | "FAILURE";
export type AuditDateRange = "TODAY" | "7_DAYS" | "30_DAYS" | "CUSTOM";
export type AuditModuleKey =
  | "ALL"
  | "AUTH"
  | "PERMISSION"
  | "MAILBOX"
  | "AI"
  | "RISK"
  | "IMPORT"
  | "CONFIG"
  | "AUDIT"
  | "OTHER";
export type AuditActionGroup =
  | "ALL"
  | "CREATE"
  | "UPDATE"
  | "TEST"
  | "LOGIN"
  | "PUBLISH"
  | "ROLLBACK"
  | "EXPORT"
  | "OTHER";
export type AuditExportFormat = "XLSX" | "CSV";

export interface AuditLogSummary {
  todayCount: number;
  yesterdayCount: number;
  dayChange: number;
  failedCount: number;
  sensitiveCount: number;
  activeActorCount: number;
  systemAdminActorCount: number;
}

export interface AuditLogOption {
  value: string;
  label: string;
  count: number;
}

export interface AuditLogOptions {
  modules: AuditLogOption[];
  actions: AuditLogOption[];
}

export interface AuditLogListItem {
  id: string;
  eventId: string;
  createdAt: string;
  actorName: string;
  actorAccount: string | null;
  actorRole: string | null;
  module: AuditModuleKey;
  moduleLabel: string;
  rawModule: string;
  action: string;
  actionLabel: string;
  actionGroup: AuditActionGroup;
  resourceType: string;
  resourceId: string | null;
  resourceLabel: string;
  summary: string;
  result: AuditLogResult;
  traceId: string;
  clientIp: string;
  client: string;
  errorCode: string | null;
  isSensitive: boolean;
}

export interface AuditLogDetail extends AuditLogListItem {
  beforeSnapshot: Record<string, unknown> | null;
  afterSnapshot: Record<string, unknown> | null;
  beforeSummary: string;
  afterSummary: string;
  context: string;
  previousHash: string | null;
  integrityHash: string | null;
}

export interface AuditLogIntegrity {
  status: "VALID" | "INVALID";
  totalRecords: number;
  verifiedRecords: number;
  firstBrokenEventId: string | null;
  lastVerifiedAt: string;
  appendOnly: true;
}

export interface AuditLogExportRequest {
  keyword?: string;
  module?: AuditModuleKey;
  action?: AuditActionGroup;
  result?: AuditLogResult;
  dateRange?: AuditDateRange;
  startDate?: string;
  endDate?: string;
  sensitiveOnly?: boolean;
  format: AuditExportFormat;
  reason: string;
}

export type MailboxProvider = "QQ" | "IMAP";
export type MailboxEncryption = "SSL" | "STARTTLS";
export type MailboxConnectionStatus = "UNTESTED" | "HEALTHY" | "FAILED";
export type MailSyncStatus = "QUEUED" | "RUNNING" | "SUCCESS" | "PARTIAL" | "FAILURE";
export type MailSyncTrigger = "MANUAL" | "SCHEDULED" | "RETRY";
export type MailMessageStatus = "ANALYZING" | "COMPLETED" | "SKIPPED" | "FAILED";
export type MailRiskCandidateStatus = "PENDING" | "CONFIRMED" | "IGNORED";

export interface MailboxConfigInput {
  provider: MailboxProvider;
  email: string;
  authCode?: string;
  imapHost: string;
  imapPort: number;
  encryption: MailboxEncryption;
  folder: string;
  subjectKeywords: string[];
  senderRule?: string;
  initialSyncWeeks: 1 | 4 | 8 | 12;
  readAttachments: boolean;
  aiExtractionEnabled: boolean;
}

export interface MailboxOverview extends Omit<MailboxConfigInput, "authCode"> {
  configured: boolean;
  maskedEmail: string | null;
  hasAuthCode: boolean;
  authCodeLast4: string | null;
  enabled: boolean;
  autoSyncEnabled: boolean;
  autoSyncIntervalMinutes: number;
  connectionStatus: MailboxConnectionStatus;
  lastTestAt: string | null;
  lastTestLatencyMs: number | null;
  lastTestErrorCode: string | null;
  lastTestErrorSummary: string | null;
  lastSyncAt: string | null;
  lastSyncStatus: MailSyncStatus | null;
  lastSyncNewCount: number;
  lastSyncSuccessCount: number;
  lastSyncRiskCandidateCount: number;
  lastSyncFailedCount: number;
  nextSyncAt: string | null;
  uidCursor: string | null;
  totalSyncedCount: number;
  totalRiskCandidateCount: number;
  updatedAt: string | null;
}

export interface MailboxConnectionTestResult {
  success: boolean;
  status: MailboxConnectionStatus;
  latencyMs: number;
  testedAt: string;
  folder: string;
  errorCode: string | null;
  errorSummary: string | null;
}

export interface MailSyncBatchItem {
  id: string;
  code: string;
  trigger: MailSyncTrigger;
  status: MailSyncStatus;
  createdAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  scannedCount: number;
  newCount: number;
  successCount: number;
  skippedCount: number;
  failedCount: number;
  riskCandidateCount: number;
  errorSummary: string | null;
}

export interface MailSyncSummary {
  configured: boolean;
  maskedEmail: string | null;
  latestBatch: MailSyncBatchItem | null;
  latestScannedCount: number;
  latestNewCount: number;
  latestSuccessCount: number;
  latestSkippedCount: number;
  latestDuplicateCount: number;
  latestRuleMismatchCount: number;
  latestFailedCount: number;
  latestRiskCandidateCount: number;
  latestPendingRiskCount: number;
  historicalFailedCount: number;
}

export interface MailProjectMatchItem {
  id: string;
  projectId: string;
  projectName: string;
  matchType: "EXACT" | "ALIAS" | "FUZZY" | "MANUAL";
  confidence: number;
  matchedText: string;
}

export interface MailAttachmentItem {
  name: string;
  type: string;
  sizeBytes: number;
  status: "PARSED" | "SKIPPED" | "FAILED";
  summary: string | null;
}

export interface MailProcessingTraceItem {
  stage: string;
  status: "COMPLETED" | "SKIPPED" | "FAILED" | "RUNNING";
  detail: string;
  occurredAt: string;
}

export interface MailRiskCandidateItem {
  id: string;
  projectId: string;
  projectName: string;
  categoryId: string;
  categoryName: string;
  level: ProjectRiskLevel;
  levelLabel: string;
  description: string;
  evidence: string;
  suggestion: string;
  confidence: number;
  status: MailRiskCandidateStatus;
  confirmedRiskId: string | null;
  reviewedAt: string | null;
}

export interface MailMessageListItem {
  id: string;
  batchId: string;
  batchCode: string;
  status: MailMessageStatus;
  subject: string;
  senderName: string | null;
  senderAddress: string | null;
  sentAt: string | null;
  processedAt: string | null;
  projectMatches: MailProjectMatchItem[];
  riskCandidateCount: number;
  pendingRiskCount: number;
  resultLabel: string;
  resultNote: string;
  failureSummary: string | null;
}

export interface MailMessageListResponse extends PaginatedResponse<MailMessageListItem> {
  historicalFailedCount: number;
}

export interface MailMessageDetail extends MailMessageListItem {
  keyPoints: string[];
  sanitizedSummary: string | null;
  attachments: MailAttachmentItem[];
  processingTrace: MailProcessingTraceItem[];
  riskCandidates: MailRiskCandidateItem[];
  retryCount: number;
}

export interface MailSyncBatchDetail extends MailSyncBatchItem {
  operatorName: string;
  durationMs: number | null;
  startUid: string | null;
  endUid: string | null;
  messages: MailMessageListItem[];
}

export interface MailRiskCandidateUpdateInput {
  projectId: string;
  categoryId: string;
  level: ProjectRiskLevel;
  description: string;
  evidence: string;
  suggestion: string;
}

export interface MailRiskReviewOptions {
  projects: Array<{ id: string; name: string }>;
  categories: Array<{ id: string; name: string }>;
  levels: Array<{ value: ProjectRiskLevel; label: string }>;
}

/**
 * Frozen FastAPI OpenAPI authority (T032).
 *
 * The generated module is the sole post-cutover contract authority and is
 * rebuilt from `openapi/openapi.json` by `pnpm contracts:gen`; it must never
 * be hand-edited. The hand-written types above remain the transitional
 * compatibility baseline until the frontend cutovers (T033/T034) consume the
 * generated surface directly.
 */
export type * as OpenApi from "./generated/openapi.js";
