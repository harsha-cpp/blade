import { useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp } from "lucide-react";
import { policyApi, type PolicyRule, type PolicySet } from "../api/policy";
import {
  describeConditions,
  isDirty,
  moveRule,
  newRule,
  removeRule,
  renumber,
  reseedDraft,
  seedDraft,
  toPolicySetIn,
  updateRule,
  validateRules,
  versionForSave,
  type DraftRule,
} from "../lib/policy";
import { errorMessage } from "../lib/errors";
import { useAuth } from "../lib/auth";
import PageHeader from "../components/PageHeader";
import ActionPill from "../components/ActionPill";
import RuleEditor from "../components/policy/RuleEditor";
import Simulator from "../components/policy/Simulator";

export default function PolicyRoute() {
  const qc = useQueryClient();
  const { can } = useAuth();
  const canEdit = can("editPolicy");
  const { data, isLoading, error } = useQuery({
    queryKey: ["policy"],
    queryFn: policyApi.get,
  });
  const defaults = useQuery({
    queryKey: ["policy-defaults"],
    queryFn: policyApi.defaults,
  });

  const [draft, setDraft] = useState<DraftRule[] | null>(null);
  const [baseline, setBaseline] = useState<PolicySet | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [matched, setMatched] = useState<string[]>([]);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [confirmReset, setConfirmReset] = useState(false);

  const adopt = useCallback((next: PolicySet) => {
    setBaseline(next);
    setDraft((previous) =>
      previous ? reseedDraft(previous, next.rules) : seedDraft(next.rules),
    );
  }, []);

  useEffect(() => {
    if (!data) return;
    if (draft === null || baseline === null) {
      adopt(data);
      return;
    }
    const serverMoved = isDirty(baseline, data.rules);
    if (serverMoved && !isDirty(baseline, draft)) adopt(data);
  }, [data, draft, baseline, adopt]);

  const save = useMutation({
    mutationFn: (rules: DraftRule[]) =>
      policyApi.replace(toPolicySetIn(rules, versionForSave(data))),
    onSuccess: async (res) => {
      setSaveError(null);
      adopt(res);
      qc.setQueryData(["policy"], res);
      await qc.invalidateQueries({ queryKey: ["policy"] });
    },
    onError: (err) =>
      setSaveError(errorMessage(err, "Could not save the policy.")),
  });

  const reset = useMutation({
    mutationFn: policyApi.reset,
    onSuccess: async (res) => {
      setConfirmReset(false);
      setSaveError(null);
      adopt(res);
      qc.setQueryData(["policy"], res);
      await qc.invalidateQueries({ queryKey: ["policy"] });
    },
    onError: (err) =>
      setSaveError(errorMessage(err, "Could not reset the policy.")),
  });

  const rules = useMemo(() => draft ?? [], [draft]);
  const dirty = useMemo(
    () => isDirty(baseline ?? undefined, rules),
    [baseline, rules],
  );
  const serverChanged = useMemo(
    () => Boolean(baseline && data && isDirty(baseline, data.rules)),
    [baseline, data],
  );
  const problems = useMemo(() => validateRules(rules), [rules]);

  function patch(next: DraftRule[]) {
    setDraft(next);
  }

  const lede = (
    <>
      What Blade does when it finds patient data in an outgoing message.{" "}
      {data ? <span className="mono subtle">{data.version}</span> : null}
    </>
  );

  if (isLoading) {
    return (
      <div>
        <PageHeader section="Configure" title="Policy" lede={lede} />
        <div className="card">
          <div className="skeleton skeleton-text" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <PageHeader section="Configure" title="Policy" lede={lede} />
        <div className="error">
          {errorMessage(error, "Could not load the policy.")}
        </div>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        section="Configure"
        title="Policy"
        lede={lede}
        actions={
          <>
            {data?.is_custom ? (
              <span className="badge">customised</span>
            ) : (
              <span className="badge">built-in defaults</span>
            )}
            {dirty && (
              <span className="badge badge-danger">Unsaved changes</span>
            )}
            {canEdit && (
              <>
                <button
                  type="button"
                  className="btn btn-sm"
                  onClick={() => data && adopt(data)}
                  disabled={!dirty || save.isPending}
                >
                  Discard edits
                </button>
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={() => {
                    setSaveError(null);
                    save.mutate(rules);
                  }}
                  disabled={!dirty || problems.length > 0 || save.isPending}
                >
                  {save.isPending ? "Saving..." : "Save policy"}
                </button>
              </>
            )}
          </>
        }
      />

      {saveError && (
        <div className="error" style={{ marginBottom: 16 }}>
          {saveError}
        </div>
      )}
      {serverChanged && (
        <div className="callout callout-warn" style={{ marginBottom: 16 }}>
          <strong>This policy was changed on the server.</strong> Reload to see
          the latest, or keep editing and your save will overwrite it.
          <div style={{ marginTop: 10 }}>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => data && adopt(data)}
            >
              Reload
            </button>
          </div>
        </div>
      )}
      {problems.length > 0 && (
        <div className="error" style={{ marginBottom: 16 }}>
          <strong>Fix before saving:</strong>
          <ul>
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="policy-layout">
        <div className="card">
          <div className="first-match-banner">
            <strong>First match wins.</strong>
            <span>
              Blade walks this list top to bottom and stops at the first enabled
              rule that matches. Moving a rule up can silence every rule below
              it.
            </span>
          </div>

          <div className="col gap-2">
            {rules.map((rule, index) => (
              <RuleRow
                key={rule.key}
                rule={rule}
                index={index}
                total={rules.length}
                canEdit={canEdit}
                expanded={expanded === rule.key}
                matched={matched.includes(rule.id)}
                onToggleExpand={() =>
                  setExpanded(expanded === rule.key ? null : rule.key)
                }
                onMove={(dir) => patch(moveRule(rules, index, dir))}
                onChange={(p) => patch(updateRule(rules, rule.key, p))}
                onRemove={() => {
                  setExpanded(null);
                  patch(removeRule(rules, rule.key));
                }}
              />
            ))}
          </div>

          {canEdit && (
            <div
              className="row gap-2"
              style={{ marginTop: 16, flexWrap: "wrap" }}
            >
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => {
                  const rule = newRule(rules);
                  patch(renumber([...rules, rule]));
                  setExpanded(rule.key);
                }}
              >
                Add rule at the bottom
              </button>
              {!confirmReset ? (
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => setConfirmReset(true)}
                >
                  Reset to defaults
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="btn btn-danger btn-sm"
                    onClick={() => reset.mutate()}
                    disabled={reset.isPending}
                  >
                    {reset.isPending
                      ? "Resetting..."
                      : `Replace all ${rules.length} rules with the ${defaults.data?.rules.length ?? ""} built-in ones`}
                  </button>
                  <button
                    type="button"
                    className="btn btn-ghost btn-sm"
                    onClick={() => setConfirmReset(false)}
                  >
                    Cancel
                  </button>
                </>
              )}
            </div>
          )}
        </div>

        <Simulator
          draft={rules}
          saved={data?.rules ?? []}
          version={data?.version ?? "custom"}
          dirty={dirty}
          onDraftMatches={setMatched}
        />
      </div>
    </div>
  );
}

function RuleRow({
  rule,
  index,
  total,
  canEdit,
  expanded,
  matched,
  onToggleExpand,
  onMove,
  onChange,
  onRemove,
}: {
  rule: DraftRule;
  index: number;
  total: number;
  canEdit: boolean;
  expanded: boolean;
  matched: boolean;
  onToggleExpand: () => void;
  onMove: (dir: -1 | 1) => void;
  onChange: (patch: Partial<PolicyRule>) => void;
  onRemove: () => void;
}) {
  return (
    <div
      className={`rule-card${rule.enabled ? "" : " is-disabled"}${matched ? " is-matched" : ""}`}
    >
      <div className="col gap-2" style={{ alignItems: "center" }}>
        <span className="rule-rank" title={`Evaluated ${ordinal(index + 1)}`}>
          {index + 1}
        </span>
        {canEdit && (
          <div className="rule-move">
            <button
              type="button"
              onClick={() => onMove(-1)}
              disabled={index === 0}
              aria-label={`Move ${rule.id} earlier`}
            >
              <ChevronUp size={12} />
            </button>
            <button
              type="button"
              onClick={() => onMove(1)}
              disabled={index === total - 1}
              aria-label={`Move ${rule.id} later`}
            >
              <ChevronDown size={12} />
            </button>
          </div>
        )}
      </div>

      <div style={{ minWidth: 0 }}>
        <div className="row gap-2" style={{ flexWrap: "wrap" }}>
          <ActionPill action={rule.action} />
          <span className="mono" style={{ fontSize: 13 }}>
            {rule.id}
          </span>
          {matched && (
            <span className="badge badge-ok">matches your preview</span>
          )}
          {!rule.enabled && <span className="badge">disabled</span>}
        </div>
        {rule.description && (
          <div className="muted" style={{ marginTop: 4, fontSize: 13 }}>
            {rule.description}
          </div>
        )}
        <div className="rule-conditions">
          {describeConditions(rule.conditions).map((text) => (
            <span key={text} className="cond">
              {text}
            </span>
          ))}
        </div>
        {expanded && canEdit && (
          <RuleEditor rule={rule} onChange={onChange} onRemove={onRemove} />
        )}
      </div>

      {canEdit && (
        <div className="col gap-2" style={{ alignItems: "flex-end" }}>
          <label className="row gap-1 subtle" style={{ cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={rule.enabled}
              onChange={(e) => onChange({ enabled: e.target.checked })}
              aria-label={`Enable ${rule.id}`}
            />
            on
          </label>
          <button
            type="button"
            className="btn btn-ghost btn-sm"
            onClick={onToggleExpand}
          >
            {expanded ? "Done" : "Edit"}
          </button>
        </div>
      )}
    </div>
  );
}

function ordinal(n: number): string {
  const suffix =
    n % 10 === 1 && n % 100 !== 11
      ? "st"
      : n % 10 === 2 && n % 100 !== 12
        ? "nd"
        : n % 10 === 3 && n % 100 !== 13
          ? "rd"
          : "th";
  return `${n}${suffix}`;
}
