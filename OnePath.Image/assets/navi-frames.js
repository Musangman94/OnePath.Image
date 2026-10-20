/* Refined from several compact ASCII silhouettes: oval cat face, square visible torso, four paws. */
(() => {
  const frame = (...rows) => rows.map((row) => row.padEnd(22, ' ')).join('\n');
  const pose = ({ eyes = 'o.o', mouth = ' ^ ', tail = '\\_', legs = ' /|   |   |   |\\', paws = ' |_|  |_|  |_|  |_|', detail = '' } = {}) => frame(
    '  /\\___/\\',
    `( ${eyes} )${detail}`,
    `(  ${mouth.trim()}  )`,
    '  |-----|',
    '  |     |',
    `  |_____|${tail}`,
    legs,
    paws
  );
  const idle = [
    pose(),
    pose({ tail: '~_' }),
    pose({ eyes: '-.-', tail: '\\_' }),
    pose({ tail: '/~' })
  ];
  const walking = [
    pose({ legs: ' /|  /|  /|  /|', paws: '/_| /_| /_| /_|' }),
    pose({ legs: '  |\\  /|  |\\  /|', paws: ' _|/ \\_| _|/ \\_|' }),
    pose({ legs: '  \\|  |/  \\|  |/', paws: ' \\_| /_| \\_| /_|' }),
    pose({ legs: ' /|  |\\  /|  |\\', paws: '/_| \\_| /_| \\_|' }),
    pose({ legs: '  |/  \\|  |/  \\|', paws: ' _|\\ /_| _|\\ /_|' }),
    pose({ legs: ' \\|  \\|  /|  /|', paws: ' \\_| \\_| /_| /_|' }),
    pose({ legs: ' /|  /|  /|  /|', paws: '/_| /_| /_| /_|' }),
    pose({ legs: '  |   |   |   |', paws: ' |_|  |_|  |_|  |_|' })
  ];
  window.ONEPATH_NAVI_FRAMES = Object.freeze({
    idle,
    walking,
    working: [
      pose({ eyes: 'o_o', detail: '  [=]' }),
      pose({ eyes: 'o.o', detail: '  [#]' }),
      pose({ eyes: 'o_o', detail: '  [=]' }),
      pose({ eyes: 'o.o', detail: '  [.]' })
    ],
    coffee: [
      pose({ detail: '  (c)' }),
      pose({ detail: '  (c)~' }),
      pose({ eyes: '-.-', detail: '  (c)' }),
      pose({ detail: '  (c)~' })
    ],
    sleeping: [
      pose({ eyes: '-.-', tail: '\\_ z' }),
      pose({ eyes: '-.-', tail: '\\_ Z' }),
      pose({ eyes: '-.-', tail: '\\_ z Z' }),
      pose({ eyes: '-.-', tail: '~_ z' })
    ],
    meditating: [
      pose({ eyes: '-.-', mouth: ' ~ ', tail: '\\_ *' }),
      pose({ eyes: '-.-', mouth: ' ~ ', tail: '\\_ +' }),
      pose({ eyes: '-.-', mouth: ' ~ ', tail: '\\_ .' }),
      pose({ eyes: '-.-', mouth: ' ~ ', tail: '~_ *' })
    ]
  });
})();
