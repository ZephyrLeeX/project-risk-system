# 项目风险管理平台

## Repository map

- `project-risk-system/`：可部署的 pnpm workspace，包含 Vue 前端、NestJS 参考后端、Prisma schema 和基础设施配置；
- `docs/adr/`：已批准的架构决策；
- `docs/implementation/`：FastAPI 重写的 implementation baseline、约束、Task Graph 和任务说明；
- `docs/fastapi-backend-design.md`：FastAPI 后端设计基线；
- `docs/specifications/`：仍需保留的产品规格和 Excel 导入规则；
- `ui-prototype/`：11 页静态视觉与交互参考，不是生产代码；
- `tools/`：规格文档与原型检查辅助脚本。

开发命令和环境初始化方式见 `project-risk-system/README.md`。本地依赖、构建结果、QA 渲染物、导入文件和其他运行数据不进入 Git，可按需重新生成。
