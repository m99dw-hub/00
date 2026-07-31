/**
 * Most Discord <-> orchestrator.
 *
 * - Odbiera wiadomosci od Ciebie na Discordzie (DM do bota), przekazuje je
 *   do orchestratora (POST /task na FastAPI).
 * - Wystawia lokalny endpoint /send, na ktory orchestrator wysyla statusy
 *   posrednie i wynik koncowy, a my przekazujemy je z powrotem na Discord.
 *
 * Uruchamianie: node index.js
 */
import 'dotenv/config'
import express from 'express'
import { Client, GatewayIntentBits, Partials } from 'discord.js'

const ORCHESTRATOR_URL = `http://${process.env.ORCHESTRATOR_HOST || '127.0.0.1'}:${process.env.ORCHESTRATOR_PORT || 8000}/task`
const BRIDGE_PORT = process.env.BRIDGE_PORT || 3000
const ALLOWED_USER_ID = process.env.ALLOWED_DISCORD_USER_ID
const BOT_TOKEN = process.env.DISCORD_BOT_TOKEN

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.DirectMessages,
    GatewayIntentBits.MessageContent,
  ],
  partials: [Partials.Channel],
})

client.on('ready', () => {
  console.log(`Polaczono z Discord jako ${client.user.tag}`)
})

client.on('messageCreate', async (msg) => {
  if (msg.author.bot) return
  if (ALLOWED_USER_ID && msg.author.id !== ALLOWED_USER_ID) {
    console.log(`Odrzucono wiadomosc od nieautoryzowanego uzytkownika: ${msg.author.id}`)
    return
  }

  const text = msg.content?.trim()
  if (!text) return

  const chatId = msg.channel.id

  try {
    const resp = await fetch(ORCHESTRATOR_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ chat_id: chatId, text, user_id: msg.author.id }),
    })
    if (!resp.ok) {
      await msg.channel.send(`⚠️ Orchestrator odrzucił zadanie (HTTP ${resp.status}).`)
    }
  } catch (err) {
    await msg.channel.send(`⚠️ Nie mogę połączyć się z orchestratorem: ${err.message}`)
  }
})

// --- HTTP API, po ktorym orchestrator wysyla statusy z powrotem ---
const app = express()
app.use(express.json())

app.post('/send', async (req, res) => {
  const { chat_id, text } = req.body
  try {
    const channel = await client.channels.fetch(chat_id)
    await channel.send(text)
    res.json({ status: 'sent' })
  } catch (err) {
    res.status(500).json({ error: err.message })
  }
})

app.get('/health', (req, res) => res.json({ status: 'ok', connected: client.isReady() }))

app.listen(BRIDGE_PORT, '127.0.0.1', () => {
  console.log(`Most Discord nasluchuje na 127.0.0.1:${BRIDGE_PORT}`)
})

client.login(BOT_TOKEN)
