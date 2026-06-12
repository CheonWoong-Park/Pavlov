/* Ghidra headless post-script: decompile every non-thunk, non-external function
 * and dump {name, demangled, entry, size, pseudo} records as JSON lines.
 *
 * Output path comes from script arg 0 (analyzeHeadless ... -postScript ExportPseudoC.java <out.jsonl>).
 * @category Pavlov
 */

import java.io.PrintWriter;
import java.io.FileWriter;

import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;

public class ExportPseudoC extends GhidraScript {

    private static String jsonEscape(String s) {
        StringBuilder b = new StringBuilder(s.length() + 16);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                default:
                    if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
                    else b.append(c);
            }
        }
        return b.toString();
    }

    @Override
    public void run() throws Exception {
        String[] args = getScriptArgs();
        String outPath = args.length > 0 ? args[0]
                : currentProgram.getExecutablePath() + ".pseudo.jsonl";

        DecompInterface decomp = new DecompInterface();
        DecompileOptions opts = new DecompileOptions();
        decomp.setOptions(opts);
        decomp.toggleCCode(true);
        decomp.toggleSyntaxTree(false);
        decomp.openProgram(currentProgram);

        int exported = 0, failed = 0;
        try (PrintWriter out = new PrintWriter(new FileWriter(outPath, true))) {
            FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
            while (it.hasNext() && !monitor.isCancelled()) {
                Function f = it.next();
                if (f.isThunk() || f.isExternal()) continue;
                DecompileResults res = decomp.decompileFunction(f, 60, monitor);
                String pseudo = null;
                if (res != null && res.decompileCompleted() && res.getDecompiledFunction() != null) {
                    pseudo = res.getDecompiledFunction().getC();
                }
                if (pseudo == null) { failed++; continue; }
                String demangled = f.getName(true);
                out.println("{\"program\":\"" + jsonEscape(currentProgram.getName())
                        + "\",\"name\":\"" + jsonEscape(f.getName())
                        + "\",\"demangled\":\"" + jsonEscape(demangled)
                        + "\",\"entry\":\"" + f.getEntryPoint()
                        + "\",\"size\":" + f.getBody().getNumAddresses()
                        + ",\"pseudo\":\"" + jsonEscape(pseudo) + "\"}");
                exported++;
            }
        }
        decomp.dispose();
        println("ExportPseudoC: exported=" + exported + " failed=" + failed + " -> " + outPath);
    }
}
