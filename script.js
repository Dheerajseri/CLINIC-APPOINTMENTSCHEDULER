// === Clinic Scheduler Frontend ===
// ⚙️ Change this to your Flask or FastAPI backend URL:
const API_BASE = "https://your-backend-url.com";  // e.g., https://clinic-scheduler.onrender.com

async function runScheduler() {
  const date = document.getElementById("schedule-date").value;
  if (!date) {
    alert("Please select a date.");
    return;
  }

  setOutput("Running scheduler...");

  try {
    const res = await fetch(`${API_BASE}/schedule`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ date }),
    });
    const data = await res.json();
    if (data.success) {
      setOutput(`✅ ${data.message}\n\n${JSON.stringify(data.data, null, 2)}`);
    } else {
      setOutput(`⚠️ Scheduling failed:\n${data.message}`);
    }
  } catch (err) {
    setOutput("❌ Error: " + err);
  }
}

async function viewAppointments() {
  const date = document.getElementById("schedule-date").value;
  if (!date) {
    alert("Please select a date.");
    return;
  }
  setOutput("Loading appointments...");
  try {
    const res = await fetch(`${API_BASE}/appointments?date=${date}`);
    const data = await res.json();
    setOutput(`📅 Appointments on ${date}:\n\n${JSON.stringify(data, null, 2)}`);
  } catch (err) {
    setOutput("❌ Error loading appointments: " + err);
  }
}

async function populateSample() {
  setOutput("Populating sample data...");
  try {
    const res = await fetch(`${API_BASE}/populate`);
    const data = await res.json();
    alert(data.message);
    setOutput("✅ " + data.message);
  } catch (err) {
    setOutput("❌ Error: " + err);
  }
}

function setOutput(text) {
  document.getElementById("output").textContent = text;
}
