//+------------------------------------------------------------------+
//|                             FinhubJournal_BridgeExport.mq4       |
//| Companion INDICATOR for FinhubKH MT4 investor bridge             |
//| Uses an indicator (not EA) so AutoTrading button is not required |
//+------------------------------------------------------------------+
#property strict
#property version   "1.05"
#property indicator_chart_window
#property indicator_buffers 0

input int PollEverySeconds = 2;
// How long to wait for broker login before answering "not connected".
input int ConnectWaitSeconds = 120;
// After posting "All History", wait this long for the server to fill MODE_HISTORY.
input int HistoryExpandWaitSeconds = 12;

const string RequestFile  = "finhub_bridge_request.json";
const string ResponseFile = "finhub_bridge_response.json";
const string AliveFile    = "finhub_bridge_alive.txt";

uint g_pendingSinceMs = 0;
string g_pendingRequestId = "";
uint g_historyExpandSinceMs = 0;
string g_historyExpandRequestId = "";
bool g_allHistoryPosted = false;

#import "user32.dll"
   int  GetAncestor(int hWnd, int gaFlags);
   bool PostMessageA(int hWnd, uint Msg, int wParam, int lParam);
#import

#define GA_ROOT 2
#define WM_COMMAND 0x0111
// Undocumented MT4 command: Account History → All History (forum/MQL community).
#define CMD_ALL_HISTORY 33058

//+------------------------------------------------------------------+
string JsonEscape(string s)
  {
   StringReplace(s, "\\", "\\\\");
   StringReplace(s, "\"", "\\\"");
   return(s);
  }

//+------------------------------------------------------------------+
string ReadWholeFile(string name)
  {
   int handle = FileOpen(name, FILE_READ|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE) return("");
   string out = "";
   while(!FileIsEnding(handle))
     {
      out += FileReadString(handle);
      if(!FileIsEnding(handle)) out += "\n";
     }
   FileClose(handle);
   return(out);
  }

//+------------------------------------------------------------------+
bool WriteWholeFile(string name, string contents)
  {
   FileDelete(name);
   int handle = FileOpen(name, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(handle == INVALID_HANDLE)
     {
      Print("FinhubBridge write open fail ", name, " ", GetLastError());
      return(false);
     }
   FileWriteString(handle, contents);
   FileClose(handle);
   Print("FinhubBridge wrote ", name);
   return(true);
  }

//+------------------------------------------------------------------+
string JsonGetString(string json, string key)
  {
   string needle = "\"" + key + "\":\"";
   int start = StringFind(json, needle);
   if(start < 0) return("");
   start += StringLen(needle);
   int end = StringFind(json, "\"", start);
   if(end < 0) return("");
   return(StringSubstr(json, start, end - start));
  }

//+------------------------------------------------------------------+
long JsonGetLong(string json, string key)
  {
   string needle = "\"" + key + "\":";
   int start = StringFind(json, needle);
   if(start < 0) return(0);
   start += StringLen(needle);
   while(start < StringLen(json) && StringGetCharacter(json, start) == ' ') start++;
   string digits = "";
   for(int i = start; i < StringLen(json); i++)
     {
      int ch = StringGetCharacter(json, i);
      if((ch >= '0' && ch <= '9') || ch == '-') digits += CharToString((uchar)ch);
      else break;
     }
   return((long)StringToInteger(digits));
  }

//+------------------------------------------------------------------+
string DealJson(long ticket, long orderId, long positionId, string entry,
                string typeStr, string symbol, double price, double volume,
                double profit, double swap, double commission, double sl,
                string comment, datetime when)
  {
   string json = "{";
   json += "\"ticket\":" + IntegerToString(ticket) + ",";
   json += "\"order\":" + IntegerToString(orderId) + ",";
   json += "\"position_id\":" + IntegerToString(positionId) + ",";
   json += "\"entry\":\"" + entry + "\",";
   json += "\"type\":\"" + typeStr + "\",";
   json += "\"symbol\":\"" + JsonEscape(symbol) + "\",";
   json += "\"price\":" + DoubleToString(price, 8) + ",";
   json += "\"volume\":" + DoubleToString(volume, 4) + ",";
   json += "\"profit\":" + DoubleToString(profit, 2) + ",";
   json += "\"swap\":" + DoubleToString(swap, 2) + ",";
   json += "\"commission\":" + DoubleToString(commission, 2) + ",";
   json += "\"sl\":" + DoubleToString(sl, 8) + ",";
   json += "\"comment\":\"" + JsonEscape(comment) + "\",";
   json += "\"time\":" + IntegerToString((long)when);
   json += "}";
   return(json);
  }

//+------------------------------------------------------------------+
void RequestAllAccountHistory()
  {
   // Expand Account History beyond the default "Last Month/3 Months" filter
   // so OrdersHistoryTotal() can see older closed trades.
   int chart = WindowHandle(Symbol(), Period());
   if(chart <= 0) return;
   int root = GetAncestor(chart, GA_ROOT);
   if(root <= 0) root = chart;
   PostMessageA(root, WM_COMMAND, CMD_ALL_HISTORY, 0);
   Print("FinhubBridge posted All History cmd=", CMD_ALL_HISTORY);
  }

//+------------------------------------------------------------------+
string BuildHistoryDeals(datetime fromTs, datetime toTs, int &matchedOut)
  {
   string deals = "[";
   bool first = true;
   matchedOut = 0;
   int total = OrdersHistoryTotal();
   for(int i = 0; i < total; i++)
     {
      if(!OrderSelect(i, SELECT_BY_POS, MODE_HISTORY)) continue;
      int type = OrderType();
      datetime closeTime = OrderCloseTime();
      datetime openTime = OrderOpenTime();

      if(type == 6 || type == 7)
        {
         if(openTime < fromTs || openTime > toTs) continue;
         string cashType = (type == 7) ? "3" : "2";
         string row = DealJson(
            OrderTicket(), OrderTicket(), OrderTicket(),
            "out", cashType, "", 0, 0,
            OrderProfit(), 0, 0, 0, OrderComment(), openTime
         );
         if(!first) deals += ",";
         deals += row;
         first = false;
         matchedOut++;
         continue;
        }

      if(type != OP_BUY && type != OP_SELL) continue;
      if(closeTime <= 0) continue;
      if(closeTime < fromTs || closeTime > toTs) continue;

      string side = (type == OP_BUY) ? "buy" : "sell";
      long ticket = OrderTicket();
      string inDeal = DealJson(
         ticket, ticket, ticket, "in", side, OrderSymbol(),
         OrderOpenPrice(), OrderLots(), 0, 0, 0, OrderStopLoss(),
         OrderComment(), openTime
      );
      string outDeal = DealJson(
         ticket + 1000000000, ticket, ticket, "out", side, OrderSymbol(),
         OrderClosePrice(), OrderLots(), OrderProfit(), OrderSwap(),
         OrderCommission(), OrderStopLoss(), OrderComment(), closeTime
      );
      if(!first) deals += ",";
      deals += inDeal + "," + outDeal;
      first = false;
      matchedOut++;
     }
   deals += "]";
   return(deals);
  }

//+------------------------------------------------------------------+
void HandleRequest()
  {
   if(!FileIsExist(RequestFile))
     {
      g_pendingSinceMs = 0;
      g_pendingRequestId = "";
      return;
     }
   string raw = ReadWholeFile(RequestFile);
   if(StringLen(raw) < 8) return;

   string action = JsonGetString(raw, "action");
   long expectedLogin = JsonGetLong(raw, "login");
   string requestId = JsonGetString(raw, "request_id");
   string serverWanted = JsonGetString(raw, "server");
   Print("FinhubBridge req ", action, " exp=", expectedLogin,
         " conn=", IsConnected(), " acct=", AccountNumber());

   // Indicators cannot Sleep(). Track wait time with GetTickCount instead.
   if(g_pendingRequestId != requestId || g_pendingSinceMs == 0)
     {
      g_pendingRequestId = requestId;
      g_pendingSinceMs = GetTickCount();
     }

   if(!IsConnected() || AccountNumber() <= 0)
     {
      uint waited = GetTickCount() - g_pendingSinceMs;
      if(waited < (uint)MathMax(5, ConnectWaitSeconds) * 1000)
        {
         Print("FinhubBridge waiting for connection ", waited, "ms");
         return;
        }
      string err = "MT4 not connected";
      if(StringLen(serverWanted) > 0)
         err = "MT4 not connected to " + serverWanted + " (check server name / investor password)";
      if(WriteWholeFile(ResponseFile,
         "{\"ok\":false,\"error\":\"" + JsonEscape(err) +
         "\",\"login\":" + IntegerToString(AccountNumber()) +
         ",\"request_id\":\"" + JsonEscape(requestId) + "\"}"))
         FileDelete(RequestFile);
      g_pendingSinceMs = 0;
      g_pendingRequestId = "";
      return;
     }

   long currentLogin = AccountNumber();
   if(expectedLogin > 0 && currentLogin != expectedLogin)
     {
      if(WriteWholeFile(ResponseFile,
         "{\"ok\":false,\"error\":\"Logged into wrong account\",\"login\":" +
         IntegerToString(currentLogin) + ",\"request_id\":\"" + JsonEscape(requestId) + "\"}"))
         FileDelete(RequestFile);
      g_pendingSinceMs = 0;
      g_pendingRequestId = "";
      return;
     }

   if(action == "verify" || action == "")
     {
      if(WriteWholeFile(ResponseFile,
         "{\"ok\":true,\"login\":" + IntegerToString(currentLogin) +
         ",\"server\":\"" + JsonEscape(AccountServer()) +
         "\",\"request_id\":\"" + JsonEscape(requestId) + "\"}"))
         FileDelete(RequestFile);
      g_pendingSinceMs = 0;
      g_pendingRequestId = "";
      return;
     }

   if(action == "history")
     {
      datetime fromTs = (datetime)JsonGetLong(raw, "from_ts");
      datetime toTs = (datetime)JsonGetLong(raw, "to_ts");
      if(toTs <= 0) toTs = TimeCurrent();
      // Never default to only 90 days — old accounts would look empty.
      if(fromTs <= 0) fromTs = D'2000.01.01';

      // Expand Account History once per request so older trades are loaded.
      if(g_historyExpandRequestId != requestId)
        {
         g_historyExpandRequestId = requestId;
         g_historyExpandSinceMs = GetTickCount();
         g_allHistoryPosted = false;
        }
      if(!g_allHistoryPosted)
        {
         RequestAllAccountHistory();
         g_allHistoryPosted = true;
         g_historyExpandSinceMs = GetTickCount();
         return;
        }
      uint waitMs = (uint)MathMax(3, HistoryExpandWaitSeconds) * 1000;
      if(GetTickCount() - g_historyExpandSinceMs < waitMs)
         return;

      int matched = 0;
      int loaded = OrdersHistoryTotal();
      string deals = BuildHistoryDeals(fromTs, toTs, matched);
      Print("FinhubBridge history loaded=", loaded, " matched=", matched,
            " from=", (long)fromTs, " to=", (long)toTs);
      if(WriteWholeFile(ResponseFile,
         "{\"ok\":true,\"login\":" + IntegerToString(currentLogin) +
         ",\"request_id\":\"" + JsonEscape(requestId) +
         "\",\"history_loaded\":" + IntegerToString(loaded) +
         ",\"matched\":" + IntegerToString(matched) +
         ",\"from_ts\":" + IntegerToString((long)fromTs) +
         ",\"to_ts\":" + IntegerToString((long)toTs) +
         ",\"deals\":" + deals + "}"))
         FileDelete(RequestFile);
      g_pendingSinceMs = 0;
      g_pendingRequestId = "";
      g_historyExpandRequestId = "";
      g_allHistoryPosted = false;
      return;
     }

   if(WriteWholeFile(ResponseFile,
      "{\"ok\":false,\"error\":\"Unknown action\",\"request_id\":\"" +
      JsonEscape(requestId) + "\"}"))
      FileDelete(RequestFile);
   g_pendingSinceMs = 0;
   g_pendingRequestId = "";
  }

//+------------------------------------------------------------------+
int OnInit()
  {
   int h = FileOpen(AliveFile, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(h != INVALID_HANDLE)
     {
      FileWriteString(h, "alive");
      FileClose(h);
     }
   Print("FinhubBridge OnInit conn=", IsConnected(), " acct=", AccountNumber());
   // Do not Sleep() here — Sleep is ignored in custom indicators and would
   // falsely answer "not connected" before login.ini can finish.
   EventSetTimer(MathMax(1, PollEverySeconds));
   HandleRequest();
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }

//+------------------------------------------------------------------+
void OnTimer()
  {
   HandleRequest();
  }

//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   HandleRequest();
   return(rates_total);
  }
