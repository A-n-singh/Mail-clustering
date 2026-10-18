import React, { useState } from "react";

const TEXTS = {
  needs: [
    ["What this list is", "Mail that looks like it needs you: either urgent or waiting for your reply, most important first."],
    ["How we decide", "The app reads the sender name and the subject first, and only looks at the body when those say nothing. Urgent: the subject has a deadline word such as urgent, asap, deadline, due today, action required, expires or overdue. Needs reply: the subject asks something, for example a question mark, can you, please confirm or let me know."],
    ["The score", "Every mail gets a score from 0 to 100. Higher means look at it sooner, and the line under the subject says why. Automated or bulk mail scores low unless its subject says action is needed."],
    ["Privacy", "These are simple rules. No AI service is used and nothing leaves your server."],
  ],
  groups: [
    ["What you are looking at", "Each bubble is one sender. Companies are grouped by their domain, and people on free-mail addresses by their email address."],
    ["Size and colour", "A bigger bubble means more mails. The colour is importance, from cyan (low) to red (high)."],
    ["When you click a bubble", "You see that sender's mails, or the same mails grouped by subject pattern, such as 'Your order # has shipped'. Read one sample before deciding about all of them."],
    ["Safety", "Each group keeps up to 360 mails, and older mail is pulled in as you clean. Nothing is trashed or archived without a confirm box, and every action can be undone in Action log."],
  ],
  map: [
    ["What you are looking at", "Your mail placed by meaning, not by sender. Mails that say similar things sit close together and share a colour. Each coloured group is a topic."],
    ["How topics are made", "Each mail is turned into a set of numbers from its sender name, subject and a short snippet, by a small model that runs on your own server. Dense neighbourhoods become topics and are named from their most common words. Hologram 3D and Flat map show the same data."],
    ["Grey dots and the review bucket", "Mail that fits no topic stays grey. The Review bucket lists it, most important first, so nothing is hidden."],
    ["Going inside", "Click a ball, or choose a topic and press Go inside, and the mails of that topic are grouped again into a new hologram of smaller groups. You can keep going until a group is too small to split, then you see its mails as a list. The path above the hologram shows where you are, and Back or any step in it takes you out. Topics groups by sender name, subject and snippet. Kinds groups all mail by what the subject says, so delivery mail from different shops lands together, and going inside a kind can then split it by sender. It all runs on your server and the first time can take up to a minute."],
    ["Using it", "Click a topic button to focus it. Drag to turn, scroll to zoom, hover to see a subject and click to open the mail. Lasso selects many mails at once, then pick Trash, Archive and so on. Topics rebuild every week, or press Rebuild topics."],
  ],
  digest: [
    ["What this page is", "A short summary: the mail that needs action and your biggest groups."],
    ["Where it comes from", "The same rules as Needs action. The number on the left is the importance score from 0 to 100, and the line under each subject gives the reason."],
  ],
  storage: [
    ["Tracked mail", "The size of the mail Smart Inbox knows about, using Gmail's own size estimate. It is not your whole Gmail quota."],
    ["Trash", "Trashed mail still uses space until Gmail deletes it, 30 days after it was trashed."],
    ["Biggest groups", "Sender groups ordered by total size. Start cleaning from the top to free the most space."],
  ],
  trash: [
    ["What this is", "Mail that is in Trash. Until it is deleted you can still restore it from Gmail."],
    ["Why it waits", "Gmail deletes Trash mail by itself 30 days after it was trashed."],
    ["Delete forever", "Optional and off by default. It needs Google's full-mailbox permission, the only one that allows permanent deletion. It deletes only mail already in Trash, checks each mail again first, and asks you to type a confirmation word. It cannot be undone."],
  ],
  log: [
    ["What this is", "Every bulk action the app has done for you, newest first."],
    ["Undo", "Undo puts the mails back as they were. It works while the mail is still in Gmail Trash, which is 30 days. Mail deleted forever cannot be undone."],
  ],
  account: [
    ["What this does", "It disconnects Google and erases everything this app stored: the mail index, groups, scores, topics and the action log. Your Gmail itself is not changed. This cannot be undone."],
  ],
};

/**
 * Wraps a view with a "Read how this works" button. The view is untouched until the button is pressed;
 * then the explanation replaces it, and "Show the view" brings it back.
 */
export default function Explain({ id, children }) {
  const [on, setOn] = useState(false);
  const t = TEXTS[id] || [];
  return (
    <div className={"explain" + (on ? " on" : "")}>
      <div className="explain-bar">
        <button onClick={() => setOn(!on)} aria-pressed={on}>{on ? "Show the view" : "Read how this works"}</button>
      </div>
      <div className="explain-body">
        <div className="explain-view">{children}</div>
        {on && (
          <div className="explain-text">
            {t.map(([h, p]) => (<section key={h}><h4>{h}</h4><p>{p}</p></section>))}
          </div>
        )}
      </div>
    </div>
  );
}
