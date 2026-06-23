use std::io::{self, BufRead, Write};
use std::fs::OpenOptions;
use std::path::Path;
use serde::{Deserialize, Serialize};
use serde_json::json;

// Define JSON-RPC Request/Response structs
#[derive(Deserialize, Debug)]
struct Request {
    #[allow(dead_code)]
    jsonrpc: String,
    id: Option<serde_json::Value>,
    method: String,
    params: Option<serde_json::Value>,
}

#[derive(Serialize, Debug)]
struct Response {
    jsonrpc: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    id: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    result: Option<serde_json::Value>,
    #[serde(skip_serializing_if = "Option::is_none")]
    error: Option<serde_json::Value>,
}

fn main() -> io::Result<()> {
    let stdin = io::stdin();
    let mut stdout = io::stdout();
    let mut reader = stdin.lock();

    let mut line = String::new();
    while reader.read_line(&mut line)? > 0 {
        if let Ok(req) = serde_json::from_str::<Request>(&line) {
            if let Some(res) = handle_request(&req) {
                let res_str = serde_json::to_string(&res)?;
                stdout.write_all(res_str.as_bytes())?;
                stdout.write_all(b"\n")?;
                stdout.flush()?;
            }
        }
        line.clear();
    }
    Ok(())
}

fn handle_request(req: &Request) -> Option<Response> {
    // If it is a notification (no id), we do not respond
    let id = req.id.clone();
    if id.is_none() {
        return None;
    }

    let mut error_val = None;
    let mut result_val = None;

    match req.method.as_str() {
        "initialize" => {
            result_val = Some(json!({
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "freecad-bridge-rust",
                    "version": "0.1.0"
                }
            }));
        }
        "tools/list" => {
            result_val = Some(json!({
                "tools": [
                    {
                        "name": "execute_python",
                        "description": "Execute arbitrary Python code inside the running FreeCAD GUI process.\nProvides full programmatic access to FreeCAD's document structure (App) and GUI (Gui).\n\nGuidelines for AI Agents:\n1. The variables 'App' (FreeCAD) and 'Gui' (FreeCADGui) are pre-loaded in your context. You do NOT need to import them.\n2. Always check if App.ActiveDocument is None. If None, create one using:\n   doc = App.ActiveDocument or App.newDocument(\"Unnamed\")\n3. Always call App.ActiveDocument.recompute() after adding, modifying, or deleting geometry to update the 3D viewer.\n4. For visual feedback, save a viewport dump to the system temp folder:\n   import os, tempfile\n   screenshot = os.path.join(tempfile.gettempdir(), \"freecad_screenshot.png\")\n   Gui.activeView().saveImage(screenshot, 1920, 1080, 'Current')",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "code": {
                                    "type": "string"
                                }
                            },
                            "required": ["code"]
                        }
                    },
                    {
                        "name": "execute_python_file",
                        "description": "Read a local Python script file from your disk and execute it inside FreeCAD. Allows running complex, pre-written script files.\n\nGuidelines for AI Agents:\n1. The path MUST be absolute. Always resolve relative paths to absolute paths before calling this tool.\n2. The target python file must contain standard FreeCAD scripting code.\n3. Ensure the script calls App.ActiveDocument.recompute() at the end to refresh the 3D GUI.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "filepath": {
                                    "type": "string"
                                }
                            },
                            "required": ["filepath"]
                        }
                    }
                ]
            }));
        }
        "tools/call" => {
            if let Some(params) = &req.params {
                let tool_name = params.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let args = params.get("arguments");

                match tool_name {
                    "execute_python" => {
                        let code = args.and_then(|a| a.get("code")).and_then(|v| v.as_str()).unwrap_or("");
                        let output = send_to_socket(code);
                        result_val = Some(json!({
                            "content": [
                                {
                                    "type": "text",
                                    "text": output
                                }
                            ]
                        }));
                    }
                    "execute_python_file" => {
                        let filepath = args.and_then(|a| a.get("filepath")).and_then(|v| v.as_str()).unwrap_or("");
                        if !Path::new(filepath).exists() {
                            result_val = Some(json!({
                                "content": [
                                    {
                                        "type": "text",
                                        "text": format!("Error: File not found at '{}'", filepath)
                                    }
                                ]
                            }));
                        } else {
                            match std::fs::read_to_string(filepath) {
                                Ok(code) => {
                                    let output = send_to_socket(&code);
                                    result_val = Some(json!({
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": output
                                            }
                                        ]
                                    }));
                                }
                                Err(e) => {
                                    result_val = Some(json!({
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": format!("Error reading file '{}': {}", filepath, e)
                                            }
                                        ]
                                    }));
                                }
                            }
                        }
                    }
                    _ => {
                        result_val = Some(json!({
                            "isError": true,
                            "content": [
                                {
                                    "type": "text",
                                    "text": format!("Unknown tool: {}", tool_name)
                                }
                            ]
                        }));
                    }
                }
            } else {
                error_val = Some(json!({
                    "code": -32602,
                    "message": "Invalid params"
                }));
            }
        }
        _ => {
            error_val = Some(json!({
                "code": -32601,
                "message": format!("Method not found: {}", req.method)
            }));
        }
    }

    Some(Response {
        jsonrpc: "2.0".to_string(),
        id,
        result: result_val,
        error: error_val,
    })
}

#[cfg(windows)]
fn send_to_socket(code: &str) -> String {
    use std::io::{Read, Write};
    
    let pipe_path = r"\\.\pipe\freecad_bridge_socket";
    
    let mut file = match OpenOptions::new()
        .read(true)
        .write(true)
        .open(pipe_path)
    {
        Ok(f) => f,
        Err(e) => return format!("Error: Connection to FreeCAD failed. Is the bridge running in FreeCAD? ({})", e),
    };

    if let Err(e) = file.write_all(code.as_bytes()) {
        return format!("Error: Failed to write command to socket. ({})", e);
    }
    if let Err(e) = file.flush() {
        return format!("Error: Failed to flush pipe. ({})", e);
    }

    let mut response = Vec::new();
    let mut buf = [0u8; 4096];
    loop {
        match file.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => response.extend_from_slice(&buf[..n]),
            Err(e) => {
                let raw_os = e.raw_os_error();
                // BrokenPipe (109) or os error 233 (No process on the other end of the pipe / pipe is being closed)
                // indicate the server finished writing and closed the pipe.
                if e.kind() == std::io::ErrorKind::BrokenPipe || raw_os == Some(233) || raw_os == Some(109) {
                    break;
                }
                return format!("Error: Failed to read response from FreeCAD. ({})", e);
            }
        }
    }

    String::from_utf8_lossy(&response).into_owned()
}

#[cfg(unix)]
fn send_to_socket(code: &str) -> String {
    use std::io::{Read, Write};
    use std::os::unix::net::UnixStream;
    
    let socket_path = std::env::temp_dir().join("freecad_bridge_socket");
    
    let mut stream = match UnixStream::connect(&socket_path) {
        Ok(s) => s,
        Err(e) => {
            let fallback = Path::new("/tmp/freecad_bridge_socket");
            match UnixStream::connect(fallback) {
                Ok(s) => s,
                Err(_) => return format!("Error: Connection to FreeCAD failed. Is the bridge running in FreeCAD? ({})", e),
            }
        }
    };

    if let Err(e) = stream.write_all(code.as_bytes()) {
        return format!("Error: Failed to write command to socket. ({})", e);
    }
    if let Err(e) = stream.flush() {
        return format!("Error: Failed to flush socket. ({})", e);
    }

    let mut response = String::new();
    if let Err(e) = stream.read_to_string(&mut response) {
        return format!("Error: Failed to read response from FreeCAD. ({})", e);
    }

    response
}
