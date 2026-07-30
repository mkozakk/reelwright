(() => {
  if (!window.MONTAGE_CONFIG) {
    document.getElementById("config-error").hidden = false;
    return;
  }

  async function api(path, options = {}) {
    const resp = await fetch(window.MONTAGE_CONFIG.apiBaseUrl + path, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        Authorization: "Bearer " + Auth.idToken(),
        ...options.headers,
      },
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      throw new Error(body.error || `request failed (${resp.status})`);
    }
    return resp.status === 204 ? null : resp.json();
  }

  function show(id) {
    for (const el of document.querySelectorAll("#app-view > section")) {
      el.hidden = el.id !== id;
    }
  }

  function setError(id, message) {
    const el = document.getElementById(id);
    el.hidden = !message;
    el.textContent = message || "";
  }

  // --- upload -----------------------------------------------------------

  function xhrUpload(url, method, headers, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method, url);
      for (const [key, value] of Object.entries(headers || {})) xhr.setRequestHeader(key, value);
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) onProgress(event.loaded / event.total);
      };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(xhr.getResponseHeader("ETag"));
        } else {
          reject(new Error(`upload failed (${xhr.status})`));
        }
      };
      xhr.onerror = () => reject(new Error("upload failed (network error)"));
      xhr.send(file);
    });
  }

  async function uploadSingle(created, file, onProgress) {
    await xhrUpload(created.upload_url, created.upload_method, created.upload_headers, file, onProgress);
  }

  async function uploadMultipart(created, file, onProgress) {
    const parts = [];
    let uploaded = 0;
    for (const part of created.parts) {
      const start = (part.part_number - 1) * created.part_size;
      const chunk = file.slice(start, start + created.part_size);
      const etag = await xhrUpload(part.url, "PUT", {}, chunk, (fraction) => {
        onProgress((uploaded + fraction * chunk.size) / file.size);
      });
      uploaded += chunk.size;
      parts.push({ part_number: part.part_number, etag: etag.replace(/"/g, "") });
    }
    await api(`/jobs/${created.job_id}/complete`, {
      method: "POST",
      body: JSON.stringify({ upload_id: created.upload_id, parts }),
    });
  }

  async function onUploadSubmit(event) {
    event.preventDefault();
    setError("upload-error", null);

    const file = document.getElementById("upload-file").files[0];
    if (!file) return;
    const vibe = document.getElementById("upload-vibe").value.trim();
    const prefs = {
      aspect: document.getElementById("upload-aspect").value,
      subtitles_enabled: document.getElementById("upload-subtitles").checked,
    };
    if (vibe) prefs.vibe = vibe;

    const progressWrap = document.getElementById("upload-progress");
    const progressBar = document.getElementById("upload-progress-bar");
    const progressLabel = document.getElementById("upload-progress-label");
    progressWrap.hidden = false;
    const onProgress = (fraction) => {
      progressBar.value = Math.round(fraction * 100);
      progressLabel.textContent = `${Math.round(fraction * 100)}%`;
    };

    try {
      const created = await api("/jobs", {
        method: "POST",
        body: JSON.stringify({ content_type: file.type, size: file.size, prefs }),
      });

      if (created.upload_type === "multipart") {
        // completes server-side via POST .../complete -- CompleteMultipartUpload
        // needs the job_api's own S3 credentials, the browser can't call it directly
        await uploadMultipart(created, file, onProgress);
      } else {
        // a single presigned PUT lands the object directly; S3's ObjectCreated
        // event (services/trigger) starts the pipeline on its own, no /complete call
        await uploadSingle(created, file, onProgress);
      }

      progressWrap.hidden = true;
      document.getElementById("upload-form").reset();
      await showStatus(created.job_id);
    } catch (err) {
      progressWrap.hidden = true;
      setError("upload-error", err.message);
    }
  }

  // --- job list -----------------------------------------------------------

  async function showJobs() {
    show("jobs-view");
    const list = document.getElementById("jobs-list");
    list.textContent = "loading...";
    try {
      const { jobs } = await api("/jobs");
      list.textContent = "";
      if (jobs.length === 0) {
        list.textContent = "no jobs yet";
        return;
      }
      for (const job of jobs) {
        const li = document.createElement("li");
        const btn = document.createElement("button");
        btn.type = "button";
        btn.textContent = `${job.job_id} -- ${job.status} -- ${job.created_at}`;
        btn.onclick = () => showStatus(job.job_id);
        li.appendChild(btn);
        list.appendChild(li);
      }
    } catch (err) {
      list.textContent = "failed to load jobs: " + err.message;
    }
  }

  // --- status + plan editor ------------------------------------------------

  let statusPollTimer = null;
  let currentJobId = null;
  let currentPlan = null;

  async function showStatus(jobId) {
    currentJobId = jobId;
    show("status-view");
    document.getElementById("status-job-id").textContent = jobId;
    setError("status-error", null);
    await pollStatus();
  }

  async function pollStatus() {
    if (statusPollTimer) clearTimeout(statusPollTimer);
    let job;
    try {
      job = await api(`/jobs/${currentJobId}`);
    } catch (err) {
      setError("status-error", err.message);
      return;
    }

    document.getElementById("status-value").textContent = job.status;
    if (job.error) setError("status-error", job.error);

    const player = document.getElementById("status-player");
    if (job.status === "DONE" && job.output_url) {
      player.hidden = false;
      document.getElementById("status-video").src = job.output_url;
      document.getElementById("status-download").href = job.output_url;
    } else {
      player.hidden = true;
    }

    renderPlanEditor(job.edit_plan);

    const stillRunning = !["DONE", "FAILED"].includes(job.status);
    if (stillRunning) statusPollTimer = setTimeout(pollStatus, 4000);
  }

  function renderPlanEditor(plan) {
    const editor = document.getElementById("plan-editor");
    if (!plan) {
      editor.hidden = true;
      currentPlan = null;
      return;
    }
    editor.hidden = false;
    currentPlan = plan;
    document.getElementById("plan-summary").textContent = plan.summary || "";

    const body = document.getElementById("plan-clips-body");
    body.textContent = "";
    plan.clips.forEach((clip, index) => {
      const row = document.createElement("tr");

      const cell = (value, editableKey) => {
        const td = document.createElement("td");
        if (editableKey) {
          const input = document.createElement("input");
          input.type = "number";
          input.step = "0.1";
          input.value = value;
          input.oninput = () => {
            currentPlan.clips[index][editableKey] = parseFloat(input.value);
          };
          td.appendChild(input);
        } else {
          td.textContent = value;
        }
        return td;
      };

      row.appendChild(cell(clip.source));
      row.appendChild(cell(clip.start, "start"));
      row.appendChild(cell(clip.end, "end"));
      row.appendChild(cell(clip.speed, "speed"));
      row.appendChild(cell(clip.reason));

      const removeTd = document.createElement("td");
      const removeBtn = document.createElement("button");
      removeBtn.type = "button";
      removeBtn.textContent = "remove";
      removeBtn.onclick = () => {
        currentPlan.clips.splice(index, 1);
        renderPlanEditor(currentPlan);
      };
      removeTd.appendChild(removeBtn);
      row.appendChild(removeTd);

      body.appendChild(row);
    });
  }

  async function onRerenderClick() {
    setError("status-error", null);
    try {
      await api(`/jobs/${currentJobId}/rerender`, {
        method: "POST",
        body: JSON.stringify(currentPlan),
      });
      await pollStatus();
    } catch (err) {
      setError("status-error", err.message);
    }
  }

  // --- wiring ---------------------------------------------------------------

  function init() {
    if (Auth.isSignedIn()) {
      document.getElementById("signed-out-view").hidden = true;
      document.getElementById("app-view").hidden = false;
      document.getElementById("account-bar").hidden = false;
      document.getElementById("account-email").textContent = Auth.email();
      showJobs();
    } else {
      document.getElementById("signed-out-view").hidden = false;
      document.getElementById("app-view").hidden = true;
      document.getElementById("account-bar").hidden = true;
    }
  }

  document.getElementById("sign-in-btn").onclick = () => Auth.signIn();
  document.getElementById("sign-out-btn").onclick = () => Auth.signOut();
  document.getElementById("nav-upload").onclick = () => show("upload-view");
  document.getElementById("nav-jobs").onclick = () => showJobs();
  document.getElementById("back-to-jobs-btn").onclick = () => showJobs();
  document.getElementById("upload-form").onsubmit = onUploadSubmit;
  document.getElementById("plan-rerender-btn").onclick = onRerenderClick;

  init();
})();
