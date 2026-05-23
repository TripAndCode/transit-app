import { useState } from "react";
import { Link } from "react-router-dom";
import { useAdminUsers, useDeleteUser, usePatchUser } from "../api/admin";
import { formatApiError } from "../api/client";

/** Admin: searchable user list with inline role / suspend / delete controls. */
export function AdminUsersPage() {
  const [q, setQ] = useState("");
  const { data, isLoading, error } = useAdminUsers({ q });
  const patch = usePatchUser();
  const del = useDeleteUser();

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 22, marginBottom: 16 }}>ユーザー管理</h1>
      <input
        type="search"
        placeholder="メール / 名前で検索"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ padding: 8, marginBottom: 16, width: 320 }}
      />
      {error && <div style={{ color: "var(--text-tertiary)" }}>{formatApiError(error)}</div>}
      {isLoading && <div>読み込み中...</div>}
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 14 }}>
        <thead>
          <tr style={{ background: "var(--surface-1)" }}>
            <th style={{ padding: 8, textAlign: "left" }}>メール</th>
            <th style={{ padding: 8, textAlign: "left" }}>名前</th>
            <th style={{ padding: 8, textAlign: "left" }}>ロール</th>
            <th style={{ padding: 8, textAlign: "left" }}>状態</th>
            <th style={{ padding: 8 }}></th>
          </tr>
        </thead>
        <tbody>
          {data?.users.map((u) => (
            <tr key={u.user_id} style={{ borderBottom: "1px solid var(--surface-2)" }}>
              <td style={{ padding: 8 }}>
                <Link to={`/admin/users/${u.user_id}`} style={{ color: "inherit" }}>{u.email}</Link>
              </td>
              <td style={{ padding: 8 }}>{u.name ?? "-"}</td>
              <td style={{ padding: 8 }}>
                <select
                  value={u.role}
                  onChange={(e) => patch.mutate({ uid: u.user_id, body: { role: e.target.value } })}
                >
                  <option value="user">user</option>
                  <option value="admin">admin</option>
                </select>
              </td>
              <td style={{ padding: 8 }}>
                {u.suspended_at ? (
                  <span style={{ padding: "2px 8px", background: "var(--surface-2)",
                                  borderRadius: 4, fontSize: 12 }}>停止中</span>
                ) : "—"}
              </td>
              <td style={{ padding: 8, textAlign: "right" }}>
                <button
                  onClick={() => patch.mutate({ uid: u.user_id, body: { suspended: !u.suspended_at } })}
                  style={{ marginRight: 8 }}
                >
                  {u.suspended_at ? "再開" : "停止"}
                </button>
                <button
                  onClick={() => {
                    if (confirm(`${u.email} を削除しますか？(匿名化、復元不可)`)) {
                      del.mutate(u.user_id);
                    }
                  }}
                >
                  削除
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {(patch.error || del.error) && (
        <div role="alert" style={{ marginTop: 8, padding: 8, background: "var(--surface-2)",
                                    borderRadius: 4, fontSize: 13, color: "var(--text-tertiary)" }}>
          {formatApiError(patch.error || del.error)}
        </div>
      )}
      <div style={{ marginTop: 12, color: "var(--text-tertiary)", fontSize: 12 }}>
        合計 {data?.total ?? 0} 件
      </div>
    </div>
  );
}
