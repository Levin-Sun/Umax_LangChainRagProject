export const P = {
  chat: "/api/v1/chat",
  conversations: "/api/v1/conversations",
  convMessages: "/api/v1/conversations/{conv_id}/messages",
  kb: "/api/v1/kb",
  kbDocs: "/api/v1/kb/{kb_id}/documents",
  docReprocess: "/api/v1/documents/{doc_id}/reprocess",
  docChunks: "/api/v1/documents/{doc_id}/chunks",
  models: "/api/v1/models",
  model: "/api/v1/models/{model_id}",
  usageSummary: "/api/v1/usage/summary",
  adminLogin: "/api/v1/admin/login",
  adminLogout: "/api/v1/admin/logout",
} as const;
