/**
 * Most WhatsApp <-> orchestrator.
 *
 * - Odbiera wiadomosci na WhatsApp (Baileys), przekazuje je do orchestratora
 *   (POST /task na FastAPI).
 * - Wystawia lokalny endpoint /send, na ktory orchestrator wysyla statusy
 *   posrednie i wynik koncowy, a my przekazujemy je z powrotem na WhatsApp.
 *
 * Uruchamianie: node index.js  (pierwsze uruchomienie: zeskanuj QR w terminalu)
 */
import 'dotenv/config'
import express from 'express'
import qrcode from 'qrcode-terminal'
import makeWASocket, { useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys'
import { pino } from 'pino'

const ORCHESTRATOR_URL = `http://${process.env.ORCHESTRATOR_HOST || '127.0.0.1'}:${process.env.ORCHESTRATOR_PORT || 8000}/task`
const BRIDGE_PORT = process.env.BRIDGE_PORT || 3000
const ALLOWED_JID = process.env.ALLOWED_WHATSAPP_JID

let sock

async function startSock() {
  const { state, saveCreds } = await useMultiFileAuthState('./auth')

  sock = makeWASocket({
    auth: state,
    logger: pino({ level: 'warn' }),
  })

  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update
    if (qr) {
      console.log('Zeskanuj kod QR w WhatsApp (Ustawienia -> Urzadzenia polaczone):')
      qrcode.generate(qr, { small: true })
    }
    if (connection === 'close') {
      const shouldReconnect =
        lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut
      console.log('Polaczenie zamkniete, reconnect:', shouldReconnect)
      if (shouldReconnect) startSock()
    } else if (connection === 'open') {
      console.log('Polaczono z WhatsApp.')
    }
  })

  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return
    const msg = messages[0]
    if (!msg.message || msg.key.fromMe) return

    const chatId = msg.key.remoteJid
    const text =
      msg.message.conversation || msg.message.extendedTextMessage?.text || ''
    if (!text.trim()) return

    // Prosta kontrola dostepu - tylko jeden autoryzowany numer moze zlecac zadania
    if (ALLOWED_JID && chatId !== ALLOWED_JID) {
      console.log(`Odrzucono wiadomosc od nieautoryzowanego JID: ${chatId}`)
      return
    }

    try {
      const resp = await fetch(ORCHESTRATOR_URL, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: chatId, text }),
      })
      if (!resp.ok) {
        await sock.sendMessage(chatId, { text: `⚠️ Orchestrator odrzucił zadanie (HTTP ${resp.status}).` })
      }
    } catch (err) {
      await sock.sendMessage(chatId, { text: `⚠️ Nie mogę połączyć się z orchestratorem: ${err.message}` })
    }
  })
}

// --- HTTP API, po ktorym orchestrator wysyla statusy z powrotem ---
const app = express()
app.use(express.json())

app.post('/send', async (req, res) => {
  const { chat_id, text } = req.body
  if (!sock) return res.status(503).json({ error: 'WhatsApp jeszcze nie polaczony' })
  try {
    await sock.sendMessage(chat_id, { text })
    res.json({ status: 'sent' })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/health', (req, res) => res.json({ status: 'ok', connected: !!sock }))

app.listen(BRIDGE_PORT, '127.0.0.1', () => {
  console.log(`Most WhatsApp nasluchuje na 127.0.0.1:${BRIDGE_PORT}`)
})

startSock()
