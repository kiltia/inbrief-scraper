mock-request:
    echo '{ \
      "chat_folder_link": "https://t.me/addlist/grg7NStE6881MDE6", \
      "offset_date": null, \
      "end_date": "2024-12-01T00:00:00+04:00", \
      "social": false \
    }' | kcat -J -P -b "10.96.11.51:9094" -t "inbrief.scraper.in.json"
