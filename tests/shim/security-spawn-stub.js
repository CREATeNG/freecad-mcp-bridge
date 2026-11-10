// Test double for the macOS `security` command. Loaded with node --require
// before the shim, so the shim's spawnSync("security", ...) is this one.
const fs = require("fs");
const cp = require("child_process");

const real = cp.spawnSync;
cp.spawnSync = function spawnSync(command, args, options) {
  if (command !== "security") return real.apply(this, arguments);
  const record = process.env.SECURITY_RECORD;
  if (record) {
    fs.appendFileSync(
      record,
      JSON.stringify({
        args: args || [],
        input: options && options.input ? String(options.input) : "",
      }) + "\n"
    );
  }
  if (args && args[0] === "-i") {
    return { status: 0, stdout: "", stderr: "", error: undefined };
  }
  return {
    status: 44,
    stdout: "",
    stderr: "The specified item could not be found in the keychain.\n",
    error: undefined,
  };
};
