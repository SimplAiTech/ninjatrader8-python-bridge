// ============================================================================
// ARGUS NinjaTrader Listener Strategy
// ============================================================================
// Installation:
//   1. In NinjaTrader 8: Tools → Edit NinjaScript → Strategy
//   2. Create new strategy named "ARGUSListener"
//   3. Paste this entire file, replacing the default content
//   4. Click Compile (F5)
//   5. Apply the strategy to a chart of the instrument you want to trade,
//      on the account you want to trade (any timeframe).
//      Orders are placed on THAT chart's instrument and THAT strategy's account:
//      the "instrument" and "account" fields in the JSON are informational only.
//      For several instruments or accounts, run one strategy instance per chart.
//
// How it works:
//   - Monitors Documents\NinjaTrader 8\argus_orders\ for JSON order files
//   - Executes orders using NT8's native order management
//   - Writes fill status to Documents\NinjaTrader 8\argus_status\
// ============================================================================

#region Using declarations
using System;
using System.IO;
using System.Collections.Generic;
using System.Globalization;
using NinjaTrader.Cbi;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Strategies;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class ARGUSListener : Strategy
    {
        // ---- State ----
        private FileSystemWatcher  watcher;
        private Queue<string>      pendingFiles  = new Queue<string>();
        private HashSet<string>    processed     = new HashSet<string>();
        private Dictionary<string, string> orderToSignal = new Dictionary<string, string>();
        private readonly object    queueLock     = new object();

        // ---- Configurable paths (set in strategy dialog) ----
        private string ordersFolder;
        private string statusFolder;

        // ---------------------------------------------------------------
        // Properties (shown in NT8 strategy dialog)
        // ---------------------------------------------------------------
        [NinjaScriptProperty]
        [NinjaTrader.Gui.PropertyEditor.PropertyEditorAttribute(typeof(NinjaTrader.Gui.PropertyEditor.PathEditor))]
        [Display(Name = "Orders Folder", Order = 1, GroupName = "ARGUS Settings")]
        public string OrdersFolder
        {
            get { return ordersFolder; }
            set { ordersFolder = value; }
        }

        [NinjaScriptProperty]
        [Display(Name = "Status Folder", Order = 2, GroupName = "ARGUS Settings")]
        public string StatusFolder
        {
            get { return statusFolder; }
            set { statusFolder = value; }
        }

        // ---------------------------------------------------------------
        // Lifecycle
        // ---------------------------------------------------------------
        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Name        = "ARGUS Listener";
                Description = "ARGUS NinjaTrader Node — listens for order files and executes trades";
                Calculate   = Calculate.OnBarClose;
                IsOverlay   = false;

                string docs = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments);
                OrdersFolder = Path.Combine(docs, "NinjaTrader 8", "argus_orders");
                StatusFolder = Path.Combine(docs, "NinjaTrader 8", "argus_status");
            }
            else if (State == State.DataLoaded)
            {
                // Ensure folders exist
                Directory.CreateDirectory(OrdersFolder);
                Directory.CreateDirectory(StatusFolder);

                // Watch for new order files
                watcher = new FileSystemWatcher(OrdersFolder, "*.json")
                {
                    NotifyFilter = NotifyFilters.FileName | NotifyFilters.CreationTime
                };
                watcher.Created += OnOrderFileCreated;
                watcher.EnableRaisingEvents = true;

                // Process any orders left over from before start
                foreach (string f in Directory.GetFiles(OrdersFolder, "*.json"))
                {
                    string name = Path.GetFileNameWithoutExtension(f);
                    if (!name.EndsWith("_cancel") && !processed.Contains(name))
                        lock (queueLock) { pendingFiles.Enqueue(f); }
                }

                Print("✅ ARGUS Listener started. Monitoring: " + OrdersFolder);
            }
            else if (State == State.Terminated)
            {
                if (watcher != null) { watcher.Dispose(); watcher = null; }
                Print("ARGUS Listener stopped.");
            }
        }

        // New file event (background thread) — just enqueue, process on bar
        private void OnOrderFileCreated(object sender, FileSystemEventArgs e)
        {
            string name = Path.GetFileNameWithoutExtension(e.FullPath);
            if (!name.EndsWith("_cancel"))
                lock (queueLock) { pendingFiles.Enqueue(e.FullPath); }
        }

        // ---------------------------------------------------------------
        // Main loop — called on each bar close
        // ---------------------------------------------------------------
        protected override void OnBarUpdate()
        {
            ProcessPending();
        }

        private void ProcessPending()
        {
            List<string> batch;
            lock (queueLock)
            {
                if (pendingFiles.Count == 0) return;
                batch = new List<string>(pendingFiles);
                pendingFiles.Clear();
            }

            foreach (string filePath in batch)
            {
                try { ExecuteOrderFile(filePath); }
                catch (Exception ex)
                {
                    string sid = Path.GetFileNameWithoutExtension(filePath);
                    Print("ARGUS Error on " + sid + ": " + ex.Message);
                    WriteStatus(sid, "ERROR", 0, 0, ex.Message);
                }
            }
        }

        // ---------------------------------------------------------------
        // Order execution
        // ---------------------------------------------------------------
        private void ExecuteOrderFile(string filePath)
        {
            // Small delay to ensure file is fully written
            System.Threading.Thread.Sleep(50);

            string json    = File.ReadAllText(filePath);
            string signalId = ExtractJson(json, "signal_id");

            // key by file name: an order and its later "_cancel" file share the same signal_id
            string fileKey = Path.GetFileNameWithoutExtension(filePath);
            if (processed.Contains(fileKey)) return;
            processed.Add(fileKey);

            string action   = ExtractJson(json, "action").ToUpper();
            int    qty      = 0;
            int.TryParse(ExtractJson(json, "quantity"), NumberStyles.Integer, CultureInfo.InvariantCulture, out qty);
            double sl       = ParseDouble(ExtractJson(json, "stop_loss"));
            double tp       = ParseDouble(ExtractJson(json, "take_profit"));

            // CANCEL command
            if (action == "CANCEL")
            {
                // NT8 will handle cancellation via ExitLong/ExitShort
                // closes the whole position opened by this signal (no quantity needed)
                string targetId = ExtractJson(json, "signal_id");
                ExitLong("ARGUS_EXIT_" + targetId.Substring(0, 8), "ARGUS_ENTRY_" + targetId.Substring(0, 8));
                ExitShort("ARGUS_EXIT_" + targetId.Substring(0, 8), "ARGUS_ENTRY_" + targetId.Substring(0, 8));
                WriteStatus(targetId, "CANCEL_SENT", 0, 0);
                return;
            }

            string entryName = "ARGUS_ENTRY_" + signalId.Substring(0, 8);

            // Submit market order
            if (action == "BUY")
                EnterLong(0, qty, entryName);
            else
                EnterShort(0, qty, entryName);

            // Remember mapping for OnOrderUpdate
            orderToSignal[entryName] = signalId;

            // Store SL/TP for OnExecutionUpdate
            // They are applied after fill confirmation in OnExecutionUpdate
            pendingSL[signalId] = sl;
            pendingTP[signalId] = tp;

            Print($"ARGUS: {action} {qty} submitted | SL={sl} TP={tp} [{signalId.Substring(0, 8)}]");
            WriteStatus(signalId, "SUBMITTED", 0, qty);
        }

        // Temporary SL/TP storage (applied after fill)
        private Dictionary<string, double> pendingSL = new Dictionary<string, double>();
        private Dictionary<string, double> pendingTP = new Dictionary<string, double>();

        // ---------------------------------------------------------------
        // Execution callback — apply SL/TP after fill
        // ---------------------------------------------------------------
        protected override void OnExecutionUpdate(
            Execution execution, string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            string entryName = execution.Order?.Name ?? "";
            if (!entryName.StartsWith("ARGUS_ENTRY_")) return;

            string signalId = orderToSignal.ContainsKey(entryName) ? orderToSignal[entryName] : null;
            if (signalId == null) return;

            WriteStatus(signalId, "FILLED", price, quantity);
            Print($"ARGUS: FILLED @ {price} x{quantity} [{signalId.Substring(0, 8)}]");

            // Apply bracket orders (SL + TP)
            double sl = pendingSL.ContainsKey(signalId) ? pendingSL[signalId] : 0;
            double tp = pendingTP.ContainsKey(signalId) ? pendingTP[signalId] : 0;

            if (marketPosition == MarketPosition.Long)
            {
                if (sl > 0) ExitLongStopMarket(0, true, quantity, sl, "ARGUS_SL_" + signalId.Substring(0, 8), entryName);
                if (tp > 0) ExitLongLimit(0, true, quantity, tp, "ARGUS_TP_" + signalId.Substring(0, 8), entryName);
            }
            else if (marketPosition == MarketPosition.Short)
            {
                if (sl > 0) ExitShortStopMarket(0, true, quantity, sl, "ARGUS_SL_" + signalId.Substring(0, 8), entryName);
                if (tp > 0) ExitShortLimit(0, true, quantity, tp, "ARGUS_TP_" + signalId.Substring(0, 8), entryName);
            }

            // Cleanup
            pendingSL.Remove(signalId);
            pendingTP.Remove(signalId);
        }

        // ---------------------------------------------------------------
        // Status file writer
        // ---------------------------------------------------------------
        private void WriteStatus(string signalId, string status, double price, int qty, string error = "")
        {
            string json = string.Format(
                CultureInfo.InvariantCulture,
                "{{\"signal_id\":\"{0}\",\"status\":\"{1}\",\"fill_price\":{2},\"quantity\":{3},\"error\":\"{4}\",\"timestamp\":\"{5}\"}}",
                signalId, status, price, qty, error.Replace("\"", "'"), DateTime.UtcNow.ToString("o")
            );
            File.WriteAllText(Path.Combine(StatusFolder, signalId + ".json"), json);
        }

        // ---------------------------------------------------------------
        // Minimal JSON value extractor (no external dependencies)
        // ---------------------------------------------------------------
        private string ExtractJson(string json, string key)
        {
            string search = "\"" + key + "\"";
            int idx = json.IndexOf(search, StringComparison.Ordinal);
            if (idx < 0) return "";
            idx += search.Length;
            // Skip : and whitespace
            while (idx < json.Length && (json[idx] == ':' || json[idx] == ' ')) idx++;
            bool quoted = idx < json.Length && json[idx] == '"';
            if (quoted) idx++;
            int start = idx;
            while (idx < json.Length)
            {
                if (quoted && json[idx] == '"') break;
                if (!quoted && (json[idx] == ',' || json[idx] == '}')) break;
                idx++;
            }
            return json.Substring(start, idx - start).Trim();
        }

        private double ParseDouble(string s)
        {
            if (string.IsNullOrEmpty(s)) return 0;
            double.TryParse(s.Replace(",", "."), NumberStyles.Any, CultureInfo.InvariantCulture, out double v);
            return v;
        }
    }
}
