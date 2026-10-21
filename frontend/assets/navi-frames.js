/* Fixed character grid: rounded head, low body, four short paws. */
(() => {
  const frame = rows => rows.map(row => row.padEnd(27)).join('\n');
  const cat = (eyes='o o', feet='   || ||    || ||', tail=')') => frame([
    '                           ',
    '   /\\_/\\                  ',
    `  ( ${eyes} )____             `,
    `   \\ ^ /     \\___${tail}        `,
    '   /         /             ',
    '   \\_______ /              ',
    feet,
    '   \' \'    \' \'             '
  ]);
  const rest = z => frame([
    `                      ${z}`,
    '                           ',
    '   /\\_/\\                  ',
    '  ( - - )____              ',
    '   \\ ^ /    \\__           ',
    '  /           _)           ',
    ' (___,_______/             ',
    '                           '
  ]);
  window.ONEPATH_NAVI_FRAMES = Object.freeze({
    idle:[cat(),cat('o o',undefined,'~'),cat('- -'),cat()],
    walking:[cat('o o','   /| ||    /| ||'),cat('o o','   || /|    || /|'),cat('o o','   |\\ ||    |\\ ||'),cat('o o','   || |\\    || |\\')],
    working:[cat('o o'),cat('o o',undefined,'~'),cat('- -'),cat('o o')],
    coffee:[cat(),cat('- -'),cat(),cat()],
    sleeping:[rest('z'),rest('zZ'),rest(' zZ'),rest('  Z')],
    meditating:[rest(''),rest(''),rest(''),rest('')]
  });
})();
