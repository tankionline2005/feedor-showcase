/** Telegram's page for adding the bot to one of the person's channels as an admin that may post, edit and delete
 * its posts (publishing, and changing or taking back what it published). */
export const addBotLink = (bot: string) =>
  `https://t.me/${bot}?startchannel&admin=post_messages+edit_messages+delete_messages`
