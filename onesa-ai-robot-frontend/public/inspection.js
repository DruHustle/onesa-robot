/** Mission views consume owned API records. Replay performs zero API requests. */
const $ = (id) => document.getElementById(id);
const node = (tag, text, className) => {
  const value = document.createElement(tag);
  value.textContent = text;
  if (className) value.className = className;
  return value;
};
const title = (text) => String(text).replaceAll("_", " ");
const terminal = (record) =>
  !["awaiting_approval", "running"].includes(record.outcome);
const outcome = {
  awaiting_approval: "Awaiting approval",
  running: "Inspection running",
  completed_normal: "Completed · normal readings",
  completed_findings: "Completed · findings to review",
  interrupted: "Interrupted · not completed",
  failed: "Execution failed",
};
const instant = (value) =>
  new Date(value * 1000).toLocaleString(undefined, { timeZoneName: "short" });
function pairs(element, values) {
  element.replaceChildren(
    ...values.flatMap(([label, value]) => [
      node("dt", label),
      node("dd", String(value)),
    ]),
  );
}
export function createInspection(client, { run, notice }) {
  let authenticated = false,
    epoch = 0,
    catalog = null,
    detail = null,
    status = null,
    context = null,
    reading = false,
    lastHistory = 0,
    historySignature = "",
    selected = "",
    playing = false,
    frame = 0,
    replayTime = null,
    visible = true;
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  function pause() {
    playing = false;
    cancelAnimationFrame(frame);
    $("replay-play").textContent = "Play replay";
  }
  function reset() {
    epoch++;
    authenticated = false;
    catalog = detail = status = context = null;
    selected = "";
    reading = false;
    lastHistory = 0;
    historySignature = "";
    pause();
    $("mission-detail").hidden = true;
    $("mission-empty").hidden = false;
    $("mission-approval").hidden = true;
    $("inspection-history").replaceChildren(
      node("li", "No inspection runs yet."),
    );
    $("mission-password").value = "";
    $("mission-error").textContent = "";
    $("context-file").value = "";
    $("context-summary").textContent = "";
    $("context-clear").hidden = true;
    $("inspection-skills").replaceChildren();
    for (const id of [
      "mission-reference",
      "mission-title",
      "mission-outcome",
      "mission-pending",
      "report-summary",
      "mission-provenance",
      "mission-steps",
      "mission-readings",
      "mission-findings",
      "mission-metrics",
      "mission-sources",
      "mission-limitations",
      "replay-events",
    ])
      $(id).replaceChildren();
    scene(0, null);
  }
  function scene(position, time) {
    if (Number.isFinite(position)) {
      $("scene-joint").setAttribute(
        "transform",
        `rotate(${(-position * 180) / Math.PI} 239 258)`,
      );
      $("scene-position").textContent =
        `${time === null ? "Observed joint" : "Replay calculation"}: ${position.toFixed(4)} rad`;
    }
  }
  function replayPosition(record, time) {
    const p = record.plan,
      t = Math.min(
        1,
        Math.max(
          0,
          (time - p.motion_starts_at_seconds) / p.motion_duration_seconds,
        ),
      );
    return (
      p.start_radians +
      (p.target_radians - p.start_radians) *
        (10 * t ** 3 - 15 * t ** 4 + 6 * t ** 5)
    );
  }
  function replay() {
    if (!detail || !terminal(detail.record)) return;
    const record = detail.record,
      time = Number($("replay-time").value);
    replayTime = time;
    $("replay-time-label").textContent = `${time.toFixed(2)} s`;
    scene(replayPosition(record, time), time);
    $("replay-events").replaceChildren(
      ...record.events
        .filter((e) => e.elapsed_seconds <= time)
        .map((e) =>
          node(
            "li",
            `${e.elapsed_seconds.toFixed(2)} s · ${title(e.kind)} · ${title(e.detail)}`,
          ),
        ),
    );
  }
  function play() {
    if (!detail || motion.matches || document.hidden || !visible) return;
    if (playing) {
      pause();
      return;
    }
    playing = true;
    $("replay-play").textContent = "Pause replay";
    let previous = performance.now();
    if (Number($("replay-time").value) >= Number($("replay-time").max))
      $("replay-time").value = "0";
    function tick(now) {
      if (!playing || document.hidden || !visible) {
        pause();
        return;
      }
      const slider = $("replay-time");
      slider.value = String(
        Math.min(
          Number(slider.max),
          Number(slider.value) + (now - previous) / 1000,
        ),
      );
      previous = now;
      replay();
      if (Number(slider.value) >= Number(slider.max)) {
        pause();
        return;
      }
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
  }
  function sources(record) {
    return [...record.observations, ...record.telemetry].map((o) =>
      node(
        "li",
        `${title(o.metric)} · ${title(o.source)} · ${o.source_ref} · ${instant(o.observed_at)} · quality ${o.quality} · original unit ${o.source_unit || o.unit} · declared freshness ${o.declared_freshness || "not applicable"} · connection ${o.declared_connection || "not applicable"}`,
      ),
    );
  }
  function guidance(value) {
    if (!detail || !value || value.stopped || value.active_task) return null;
    if (detail.record.outcome === "awaiting_approval")
      return Date.now() / 1000 - detail.record.plan.created_at > 15
        ? "This inspection plan expired. Cancel it and generate a fresh plan."
        : client.authenticationMethod === "onesa" ? "Review the exact inspection plan and confirm approval within 15 seconds, or cancel." : "Review the inspection plan and confirm your password within 15 seconds, or cancel.";
    if (detail.record.outcome === "running")
      return "Waiting for the final recorded inspection result.";
    return "Review this inspection’s result and read-only replay, or create a new plan.";
  }
  function render() {
    if (!detail) return;
    const { record, report } = detail,
      p = record.plan,
      done = terminal(record);
    $("mission-empty").hidden = true;
    $("mission-detail").hidden = false;
    $("mission-reference").textContent =
      `RUN ${detail.id.slice(0, 8)} · PLAN ${p.version} · SCENARIO ${p.scenario_version}`;
    $("mission-title").textContent = p.title;
    $("mission-outcome").textContent = outcome[record.outcome];
    $("mission-outcome").dataset.outcome = record.outcome;
    $("mission-pending").textContent =
      record.outcome === "awaiting_approval"
        ? client.authenticationMethod === "onesa"
          ? "Review the steps and source context below. Confirm approval using your current Onesa MFA session."
          : "Review the steps and source context below. Password confirmation is required."
        : record.outcome === "running"
          ? "Keep this page connected. Stop or cancel remains available."
          : record.outcome === "completed_findings"
            ? "The inspection executed successfully and found evidence to review. It did not issue a maintenance command."
            : record.outcome === "completed_normal"
              ? "Inspection complete. The recorded synthetic readings meet the reference comparison rules."
              : "Execution did not complete. Review the intervention reason. Reset and explicitly create a new run to continue.";
    const completed = new Set(
      record.events
        .filter((e) => e.kind === "step_completed")
        .map((e) => e.detail),
    );
    $("mission-steps").replaceChildren(
      ...p.steps.map((step) => {
        const skill = catalog?.skills.find((s) => s.id === step.skill_id);
        return node(
          "li",
          `${completed.has(step.skill_id) ? "✓ " : ""}${skill?.name || title(step.skill_id)}`,
          completed.has(step.skill_id) ? "step-complete" : "",
        );
      }),
    );
    pairs($("mission-provenance"), [
      ["Plan SHA-256", record.plan_sha256],
      ["Robot profile", `${p.profile.name} v${p.profile.version}`],
      ["Capabilities", p.profile.capabilities.join(", ")],
      ["Scenario / seed", `${p.scenario} / ${p.seed}`],
      [
        "Start → target",
        `${p.start_radians.toFixed(4)} → ${p.target_radians} rad`,
      ],
      [
        "Duration / timeout",
        `${p.duration_seconds} s / ${p.timeout_seconds} s`,
      ],
      ["Asset context", p.asset.context_kind],
      ["Source asset", p.asset.source_asset_id || p.asset.id],
      [
        "Source name / revision",
        `${p.asset.source_asset_name || "Synthetic reference"} / ${p.asset.source_asset_revision ?? p.asset.context_version}`,
      ],
      ["Source model", p.asset.source_model_id || "None"],
      ["Context source", p.asset.source],
      ["Imported file digest", p.asset.source_sha256 || "Not applicable"],
      ["Created", instant(p.created_at)],
    ]);
    $("mission-approval").hidden = record.outcome !== "awaiting_approval";
    if (guidance(status)) $("next-step").textContent = guidance(status);
    $("mission-cancel").hidden = done;
    $("mission-report").hidden = !record.observations.length && !done;
    $("report-summary").textContent =
      outcome[record.outcome] +
      ". " +
      (report.execution_complete
        ? "Execution completed; findings and unknown evidence are listed below."
        : "Partial evidence only; this is not a completed inspection.");
    $("mission-readings").replaceChildren(
      ...p.asset.measures.map((m) => {
        const row = document.createElement("tr"),
          a = record.observations.find((o) => o.metric === m.metric),
          b = record.telemetry.find((o) => o.metric === m.metric);
        for (const value of [
          title(m.metric),
          a?.value == null ? "Unavailable" : `${a.value.toFixed(3)} ${a.unit}`,
          b?.value == null ? "Unavailable" : `${b.value.toFixed(3)} ${b.unit}`,
          `${m.minimum}–${m.maximum} ${m.unit}`,
        ])
          row.append(node("td", value));
        return row;
      }),
    );
    $("mission-findings").replaceChildren(
      ...(record.findings.length
        ? record.findings.map((f) =>
            node(
              "li",
              `${title(f.kind)} · ${title(f.metric)}: ${f.explanation}`,
            ),
          )
        : [
            node(
              "li",
              report.execution_complete
                ? "No findings recorded."
                : "Comparison has not completed.",
            ),
          ]),
    );
    const metrics = report.metrics;
    pairs($("mission-metrics"), [
      ["Evaluation", metrics.scope],
      ["Recorded elapsed", `${metrics.elapsed_seconds.toFixed(2)} s`],
      [
        "Observation accuracy",
        metrics.accuracy_against_scenario === null
          ? "Not assessed"
          : `${metrics.correct_against_scenario} / ${metrics.expected_observations} scenario values`,
      ],
      ["Interventions", metrics.interventions],
      ["Recovery", title(metrics.recovery)],
    ]);
    $("mission-sources").replaceChildren(...sources(record));
    $("mission-limitations").replaceChildren(
      ...report.limitations.map((text) => node("li", text)),
    );
    $("mission-replay").hidden = !done;
    if (done) {
      const slider = $("replay-time"),
        newMax = Math.min(30, Math.max(0.05, record.elapsed_seconds));
      slider.max = String(newMax);
      if (replayTime === null) slider.value = String(newMax);
      $("replay-play").disabled = motion.matches;
      $("replay-play").title = motion.matches
        ? "Reduced motion is enabled; use the replay position control."
        : "";
      replay();
    } else if (status) scene(status.position_radians, null);
  }
  async function loadCatalog(current) {
    const data = await client.missionCatalog();
    if (current !== epoch || !authenticated) return;
    catalog = data;
    for (const [id, items, label] of [
      ["mission-profile", data.robots, "name"],
      ["mission-scenario", data.scenarios, "label"],
    ])
      $(id).replaceChildren(
        ...items.map((item) => {
          const option = node("option", item[label]);
          option.value = item.id;
          return option;
        }),
      );
    $("context-availability").textContent =
      data.asset_context_import === "enabled"
        ? "File import enabled. Imported telemetry remains a timestamped, untrusted snapshot; the execution stays synthetic."
        : "Import is disabled by runtime configuration. The complete standalone synthetic workflow is available.";
    $("context-form").hidden = data.asset_context_import !== "enabled";
    $("inspection-skills").replaceChildren(
      ...data.skills.map((skill) => {
        const article = document.createElement("article");
        article.append(
          node("h3", skill.name),
          node("p", `${skill.id} · v${skill.version}`),
          node("p", skill.completion_condition),
        );
        const extra = document.createElement("details");
        extra.append(node("summary", "Inputs, preconditions and limits"));
        for (const text of [
          ...skill.inputs,
          ...skill.preconditions,
          skill.operating_limits,
          `Timeout: ${skill.timeout_seconds} s`,
          ...skill.required_capabilities,
        ])
          extra.append(node("p", text));
        article.append(extra);
        return article;
      }),
    );
  }
  async function open(id) {
    pause();
    const current = epoch;
    const value = await client.mission(id);
    if (current !== epoch || !authenticated) return;
    selected = id;
    replayTime = null;
    detail = value;
    render();
  }
  async function history(current) {
    const list = await client.missions();
    if (current !== epoch || !authenticated) return;
    lastHistory = performance.now();
    const signature = JSON.stringify(list.missions);
    if (signature === historySignature) return;
    historySignature = signature;
    const root = $("inspection-history"),
      expanded = root.querySelector("details")?.open || false,
      focused = root.contains(document.activeElement)
        ? document.activeElement.dataset.mission
        : null;
    const items = list.missions.map((item) => {
      const li = document.createElement("li"),
        button = node(
          "button",
          `${item.asset} · ${title(item.scenario)} · ${outcome[item.outcome]}`,
          "secondary",
        );
      button.type = "button";
      button.dataset.mission = item.id;
      button.addEventListener("click", () =>
        run(async () => {
          await open(item.id);
          $("mission-detail").scrollIntoView({ block: "start" });
        }, "Recorded inspection opened."),
      );
      li.append(
        button,
        node("span", `${instant(item.created)} · ${item.finding_count} findings · ${item.id.slice(0, 8)}`),
      );
      return li;
    });
    if (items.length > 6) {
      const older = document.createElement("li"),
        disclosure = document.createElement("details"),
        list = document.createElement("ol");
      disclosure.open = expanded;
      list.append(...items.slice(6));
      disclosure.append(node("summary", `Show ${items.length - 6} older runs`), list);
      older.append(disclosure);
      root.replaceChildren(...items.slice(0, 6), older);
    } else root.replaceChildren(...(items.length ? items : [node("li", "No inspection runs yet. Create your first supervised run above.")]));
    if (focused) root.querySelector(`[data-mission="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
    if (!selected && list.missions.length) await open(list.missions[0].id);
  }
  function updateReadiness() {
    const value = status;
    const ready = authenticated && value && !value.stopped && !value.active_task && client.canConfirm?.() !== false;
    if (value) $("mission-readiness").textContent = value.stopped
      ? "Stopped · reset required"
      : value.active_task
        ? "Running · supervised simulation"
        : "Ready · simulation only";
    $("mission-submit").disabled =
      !ready || !catalog || $("mission-form").getAttribute("aria-busy") === "true";
    $("mission-approve-submit").disabled =
      !ready ||
      $("mission-approval").getAttribute("aria-busy") === "true" ||
      !detail ||
      Date.now() / 1000 - detail.record.plan.created_at > 15;
  }
  async function refresh(value) {
    status = value;
    updateReadiness();
    if (!authenticated || reading) return;
    reading = true;
    $("mission-error").textContent = "";
    const current = epoch;
    try {
      if (!catalog) await loadCatalog(current);
      if (!authenticated || current !== epoch) return;
      updateReadiness();
      if (performance.now() - lastHistory > 5000 || !lastHistory)
        await history(current);
      if (selected && detail && !terminal(detail.record)) {
        const latest = await client.mission(selected);
        if (current !== epoch || !authenticated) return;
        detail = latest;
        if (terminal(detail.record)) {
          lastHistory = 0;
          notice(
            outcome[detail.record.outcome] + ". Review the recorded result.",
          );
        }
        render();
      }
      if (status && (!detail || !terminal(detail.record)))
        scene(status.position_radians, null);
    } catch (error) {
      if (current === epoch) {
        if (error.status === 401) reset();
        $("mission-error").textContent =
          `Inspection evidence unavailable: ${error.message}`;
        $("mission-submit").disabled = true;
        $("mission-approve-submit").disabled = true;
      }
    } finally {
      if (current === epoch) reading = false;
    }
  }
  $("mission-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const current = epoch;
    run(
      async () => {
        const value = await client.planMission({
          command_id: crypto.randomUUID(),
          asset_id: $("mission-asset").value,
          robot_profile_id: $("mission-profile").value,
          scenario: $("mission-scenario").value,
          seed: Number($("mission-seed").value),
          context_id: context?.id || null,
        });
        if (current !== epoch || !authenticated) return;
        pause();
        detail = value;
        selected = value.id;
        replayTime = null;
        lastHistory = 0;
        render();
        $(client.authenticationMethod === "onesa" ? "mission-password-confirmed" : "mission-password").focus();
      },
      "Review the plan and approve within 15 seconds.",
      event.currentTarget,
    );
  });
  $("mission-approval").addEventListener("submit", (event) => {
    event.preventDefault();
    const password = $("mission-password").value,
      confirmed = Boolean($("mission-password-confirmed").checked),
      current = epoch,
      id = detail?.id,
      digest = detail?.record.plan_sha256;
    $("mission-password").value = "";
    $("mission-password-confirmed").checked = false;
    run(
      async () => {
        if (!id || !digest)
          throw new Error("Generate and review a current inspection plan.");
        const value = await client.approveMission(id, {
          ...(client.confirmation?.(password, confirmed) || {password}),
          expected_plan_sha256: digest,
        });
        if (current !== epoch || !authenticated) return;
        detail = value;
        render();
      },
      "Inspection running. Keep this page connected.",
      event.currentTarget,
    );
  });
  $("mission-cancel").addEventListener("click", () => {
    if (detail)
      run(
        () => client.cancel(detail.id),
        "Inspection cancelled; review the recorded result.",
      );
  });
  $("mission-export").addEventListener("click", () => {
    const current = epoch,
      id = detail?.id;
    if (!id) return;
    run(async () => {
      const exported = await client.exportMission(id);
      if (current !== epoch || !authenticated) return;
      const blob = new Blob([JSON.stringify(exported, null, 2) + "\n"], {
          type: "application/json",
        }),
        url = URL.createObjectURL(blob),
        link = document.createElement("a");
      link.href = url;
      link.download = `inspection-${id}.json`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }, "Inspection export prepared from the owned journal.");
  });
  $("context-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const current = epoch;
    run(
      async () => {
        const file = $("context-file").files[0];
        if (!file || file.size > 240000)
          throw new Error("Choose a robotics/v1 JSON file up to 240 KB.");
        const value = await client.importContext({
          document: await file.text(),
          mapping: {
            temperature: $("context-temperature").value,
            pressure: $("context-pressure").value,
            vibration: $("context-vibration").value,
          },
        });
        if (current !== epoch || !authenticated) return;
        context = value;
        $("context-summary").textContent =
          `Imported ${value.asset.source_asset_id}. Review ${value.asset.imported_telemetry.map((o) => `${o.metric}: ${o.value ?? "missing"} ${o.unit}, ${instant(o.observed_at)}, ${o.quality}`).join("; ")}. Digest ${value.asset.source_sha256}. It will be included in the next plan; existing plans do not change.`;
        $("context-clear").hidden = false;
        $("context-file").value = "";
      },
      "Read-only context imported; review it before creating a new plan.",
      event.currentTarget,
    );
  });
  $("context-clear").addEventListener("click", () => {
    context = null;
    $("context-summary").textContent =
      "The next plan will use synthetic telemetry. Existing plans are unchanged.";
    $("context-clear").hidden = true;
  });
  $("replay-time").addEventListener("input", () => {
    pause();
    replay();
  });
  $("replay-play").addEventListener("click", play);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) pause();
  });
  motion.addEventListener("change", () => {
    pause();
    render();
  });
  const observer = new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    if (!visible) pause();
  });
  observer.observe($("inspection-svg"));
  window.addEventListener("pagehide", pause);
  return {
    refresh,
    reset,
    authenticate() {
      reset();
      authenticated = true;
    },
    stale() {
      status = null;
      pause();
      $("mission-submit").disabled = true;
      $("mission-approve-submit").disabled = true;
      $("mission-readiness").textContent = "Disconnected · observation stale";
    },
    hasPending() {
      return detail && !terminal(detail.record);
    },
    guidance,
  };
}
