window.ER_INTEL_CONFIG = {
  supabaseUrl: "https://kiunhwuejcfddbejxbcr.supabase.co",
  supabaseAnonKey: "sb_publishable_M7FLQbINNFfoCbfsqFjAsA_f4eG2jrS",
  table: "ed_wait_snapshots",
  timezone: "America/Toronto",
  pollMs: 60000,
  hospitals: {
    cvh: { name: "Credit Valley Hospital", short: "Credit Valley", city: "Mississauga", cadence: 5, fresh: [5, 15], color: "#5ee7f2" },
    milton: { name: "Milton District Hospital", short: "Milton", city: "Milton", cadence: 15, fresh: [25, 45], color: "#70e7aa" },
    otmh: { name: "Oakville Trafalgar Memorial Hospital", short: "Oakville", city: "Oakville", cadence: 15, fresh: [25, 45], color: "#b8a5ff" }
  }
};
